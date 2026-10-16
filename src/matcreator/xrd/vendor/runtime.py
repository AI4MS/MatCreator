import asyncio
import base64
import io
import json
import os
import signal
import subprocess
import time
import zipfile
from dataclasses import replace
from pathlib import Path
from matcreator.xrd.compat import RuntimeProvider, AgentInfo, AgentStatus, AgentEvent, AgentEventType, AgentNotFoundError

class XRDRuntime(RuntimeProvider):
    def __init__(self, capability_provider):
        self.capability_provider = capability_provider
        self.records = {}
        self.processes = {}
        self.root = Path(os.environ.get("OAW_XRD_ROOT", str(Path(__file__).resolve().parents[5] / "XRD")))
        self.run_root = Path(os.environ.get("OAW_XRD_RUN_ROOT", str(self.root / "runs")))

    async def create_agent(self, config):
        from .workflow import started_at_ms
        started_at_ms[config.agent_id] = config.provider_config.get('workflow_started_at_ms', 0)
        engine = ("QualX3 + OAW" if config.provider_config.get('library_engine', 'qualx') == 'qualx' else "Peak matching") if config.provider_config.get("mode") == "match" else "OAW_XRDfit"
        self.records[config.agent_id] = AgentInfo(config, AgentStatus.IDLE, f"xrd-{config.agent_id}", details={"engine":engine, "project":str(self.root), "workflow_reset_supported": True})
        return self.records[config.agent_id]

    async def update_agent(self, config):
        return await self.create_agent(config)

    async def get_agent(self, agent_id, *, include_archive=True):
        if agent_id not in self.records:
            raise AgentNotFoundError(agent_id)
        info = self.records[agent_id]
        from .pipeline import owned_runs
        runs = [run for run in owned_runs(self.run_root, agent_id)
                if run['manifest'].get('created_at_ns', 0) / 1e6 >= info.config.provider_config.get('workflow_started_at_ms', 0)]
        if not runs:
            return info
        latest = runs[0]
        record, directory = latest['manifest'], latest['directory']
        details = {**info.details, 'last_run': record}
        details['stage_timing'] = {}
        for item in runs:
            m = item['manifest']
            stage = item['stage']
            if stage not in details['stage_timing']:
                details['stage_timing'][stage] = {'started_at_ms': m.get('created_at_ns', 0)/1e6, 'finished_at_ms': m.get('finished_at_ns', 0)/1e6, 'running': m.get('status') == 'running'}
        from .frames import read_frames
        details['frames'] = read_frames(directory)
        try:
            details['progress'] = json.loads((directory / 'progress.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        if info.config.provider_config.get('mode') == 'match':
            match = next((r for r in runs if r['stage'] == 'search' and r['manifest'].get('status') == 'completed'
                          and r['result'] and r['result'].get('mode') == 'match'), None)
            workflow = {'match_run_id': match['manifest']['run_id'] if match else '', 'active_stage': latest['stage'] or 'search'}
            if match:
                details['result'] = match['result']
                details['frame_sets'] = {'search': read_frames(match['directory'])}
                for stage in ('preopt', 'fit'):
                    attempts = [r for r in runs if r['stage'] == stage and
                                (r['manifest'].get('workflow_match_run_id') or (r['result'] or {}).get('match_run_id')) == workflow['match_run_id']]
                    def stage_entry(run):
                        value = {'run_id': run['manifest']['run_id'], 'status': run['manifest'].get('status', 'failed')}
                        if run['result']:
                            value['result'] = run['result']
                        if run['manifest'].get('error'):
                            value['error'] = run['manifest']['error']
                        return value
                    if stage == 'fit':
                        preopt_id = workflow.get('preopt', {}).get('run_id')
                        previous_fit = next((r for r in attempts if r['manifest'].get('status') == 'completed' and r['result']), None)
                        attempts = [r for r in attempts if
                                    (r['manifest'].get('workflow_preopt_run_id') or (r['result'] or {}).get('preopt_run_id')) == preopt_id]
                        if previous_fit and previous_fit not in attempts:
                            workflow['previous_fit'] = stage_entry(previous_fit)
                    if not attempts:
                        continue
                    value = stage_entry(attempts[0])
                    details['frame_sets'][stage] = read_frames(attempts[0]['directory'])
                    prior = next((r for r in attempts[1:] if r['manifest'].get('status') == 'completed' and r['result']), None)
                    if prior:
                        value['previous_success'] = stage_entry(prior)
                    workflow[stage] = value
            details['workflow'] = workflow
        elif latest['result']:
            details['result'] = latest['result']
        if include_archive and record.get('status') != 'running':
            try:
                outputs = [p for p in directory.rglob('*') if p.is_file() and p.name != 'input.json']
                if sum(p.stat().st_size for p in outputs) > 30 * 1024 * 1024:
                    details['archive_note'] = f'Results exceed 30 MiB. Open local folder: {directory}'
                else:
                    data = io.BytesIO()
                    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
                        for output in outputs:
                            archive.write(output, str(output.relative_to(directory)))
                    details['archive'] = base64.b64encode(data.getvalue()).decode()
            except OSError:
                details['archive_note'] = f'Open local result folder: {directory}'
        return replace(info, details=details)

    async def delete_agent(self, agent_id):
        from .workflow import started_at_ms
        started_at_ms.pop(agent_id, None)
        self.records.pop(agent_id, None)

    async def stop(self, run_id):
        process = self.processes.get(run_id)
        if process and process.returncode is None:
            if os.name == "nt":
                killer = await asyncio.create_subprocess_exec("taskkill.exe", "/PID", str(process.pid), "/T", "/F", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await killer.wait()
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            await process.wait()

    async def execute(self, config, context, runtime_input):
        from . import XRDConfig
        options = XRDConfig.model_validate(dict(config.provider_config))
        pipeline_stage = options.mode == 'match' and options.workflow_stage in {'preopt', 'fit'}
        if options.low_angle >= options.high_angle:
            raise ValueError("Low angle must be below high angle")
        inputs = []
        if not pipeline_stage and (options.connected_inputs or options.mode == "match"):
            for tool in await self.capability_provider.list_tools(config.agent_id):
                if tool.name != "read_xrd_input":
                    continue
                for target in (tool.input_schema or {}).get("properties", {}).get("target", {}).get("enum", []):
                    inputs.append(await self.capability_provider.invoke_tool(config.agent_id, tool.capability_id, {"target": target}))
            if options.mode == 'match':
                own = await self.capability_provider.read_own_document(config.agent_id)
                library = own['value']
                if library.get('path') or any(library.get('slots', [])):
                    inputs.append({'node_id': config.agent_id, 'revision': own['revision'], 'value': library})
            patterns = [i for i in inputs if i["value"]["kind"] == "pattern"]
            refs = [i for i in inputs if i["value"]["kind"] == "reference"]
            libraries = [i for i in inputs if i['value']['kind'] == 'library']
            if len(patterns) != 1 or not patterns[0]["value"]["points"] or not (refs or (options.mode == 'match' and libraries)):
                raise ValueError("请用「XRD 输入」连接一个已导入实验谱和标准卡片或参考谱库；拟合仍需要标准卡片与 CIF")
            if options.mode == 'match':
                from .library import validate_library, allowed_elements
                allowed_elements(options.library_elements)
                for item in libraries:
                    validate_library(item['value'])
            if any(not r["value"]["peaks"] for r in refs):
                raise ValueError("连接的标准卡片尚未导入峰表")
            if options.mode == "fit":
                cifs = [i for i in inputs if i["value"]["kind"] == "cif" and (not options.cif_node_id or i["node_id"] == options.cif_node_id)]
                if len(cifs) != 1 or not cifs[0]["value"]["text"]:
                    raise ValueError("匹配结果可保留；全谱拟合需要连接并指定一个已导入的候选 CIF")
                if cifs[0]["value"]["reference_node_id"] not in {r["node_id"] for r in refs}:
                    raise ValueError("请在 CIF 对象中明确关联本次连接的标准卡片")
                options = options.model_copy(update={"demo": False, "cif": cifs[0]["value"]["text"],
                    "intensity_csv": "\n".join(f"{x},{y}" for x, y in patterns[0]["value"]["points"])})
        if options.mode == "fit" and not options.demo and (not options.cif.strip() or not options.intensity_csv.strip()):
            raise ValueError("Upload intensity CSV and candidate CIF")
        default_python = self.root / ".venv-xrd" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python = Path(os.environ.get("OAW_XRD_PYTHON", str(default_python)))
        if not python.is_file():
            raise ValueError(f"XRD interpreter not found: {python}; configure OAW_XRD_ROOT or OAW_XRD_PYTHON")
        from .pipeline import run_directory, build_pipeline_input
        run = run_directory(self.run_root, context.run_id)
        run.mkdir(parents=True, exist_ok=False)
        (run / "input.json").write_text(options.model_dump_json(), encoding="utf-8")
        (run / "input-snapshot.json").write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
        manifest = {"run_id":context.run_id,"agent_id":config.agent_id,"engine_root":str(self.root),"status":"running",
                    'created_at_ns': time.time_ns(), 'workflow_stage': options.workflow_stage if options.mode == 'match' else 'legacy_fit',
                    'workflow_match_run_id': options.workflow_match_run_id if pipeline_stage else '',
                    'workflow_preopt_run_id': options.workflow_preopt_run_id if pipeline_stage else ''}
        (run / "oaw.json").write_text(json.dumps(manifest), encoding="utf-8")
        from .history import ACTIVE_RUNS
        ACTIVE_RUNS.add(context.run_id)
        def event(kind, payload, status=None):
            return AgentEvent(config.agent_id,context.run_id,kind,payload,run_status=status)
        yield event(AgentEventType.MESSAGE,{"text":f"XRD {options.mode} started. Results: {run}"})
        try:
            from .sql_history import capture
            await asyncio.to_thread(capture, run)
            if pipeline_stage:
                def progress(value):
                    temporary = run / 'progress.tmp'
                    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
                    temporary.replace(run / 'progress.json')
                pipeline_input = await build_pipeline_input(self.run_root, config.agent_id, options, progress=progress)
                (run / 'pipeline-input.json').write_text(json.dumps(pipeline_input, ensure_ascii=False, indent=2), encoding='utf-8')
                options = options.model_copy(update={'wavelength': pipeline_input['wavelength']})
                (run / 'input.json').write_text(options.model_dump_json(), encoding='utf-8')
            with (run / "console.log").open("wb") as log:
                process_options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
                process = await asyncio.create_subprocess_exec(str(python),"-u",str(Path(__file__).with_name("worker_monitor.py")),str(self.root),str(run),stdout=log,stderr=log, **process_options)
                self.processes[context.run_id] = process
                manifest["pid"] = process.pid
                (run / "oaw.json").write_text(json.dumps(manifest), encoding="utf-8")
                while process.returncode is None:
                    try:
                        await asyncio.wait_for(process.wait(),timeout=10)
                    except asyncio.TimeoutError:
                        await asyncio.to_thread(capture, run)
                        yield event(AgentEventType.MESSAGE,{"text":f"XRD {options.mode} running…"})
            if process.returncode != 0:
                tail=(run / "console.log").read_text(encoding="utf-8",errors="replace")[-5000:]
                raise RuntimeError(f"XRD {options.mode} exited {process.returncode}: {tail}")
            result=json.loads((run / "result.json").read_text(encoding="utf-8"))
            if options.mode == 'match' and not pipeline_stage:
                from .frames import search_frames
                yield event(AgentEventType.MESSAGE, {'text': '正在自动保存候选初始 CIF 与同步画布帧…'})
                await search_frames(run, result)
            if pipeline_stage and result.get('status') == 'failed':
                raise RuntimeError('本阶段所有候选处理失败；逐候选错误已保存，原始检索与之前结果已保留')
            manifest["status"]="completed"
            (run / 'oaw.json').write_text(json.dumps(manifest), encoding='utf-8')
            await asyncio.to_thread(capture, run, artifacts=True)
            report = result if options.mode == "fit" else ({'mode': 'pipeline', 'stage': result['stage'],
                'match_run_id': result['match_run_id'], 'candidates': [
                    {key: candidate[key] for key in ('candidate_id', 'label', 'status', 'accepted', 'converged', 'metrics', 'error') if key in candidate}
                    for candidate in result['candidates']], 'interpretation': result['interpretation']} if pipeline_stage else {"mode": "match", "library_search": result['library_search'], "observed_peaks": len(result["observed_peaks"]),
                "unexplained_peaks": len(result["unexplained_peaks"]), "candidates": [
                    {key: c[key] for key in ("filename", "matched_reference_count", "reference_count", "mean_abs_delta", "fit_input_status")}
                    for c in result["candidates"]], "interpretation": result["interpretation"]})
            text=json.dumps({"result_directory":str(run),**report},ensure_ascii=False,indent=2)
            yield event(AgentEventType.MESSAGE,{"text":text,"final":True})
            yield event(AgentEventType.COMPLETED,{"text":text},"succeeded")
        except asyncio.CancelledError:
            manifest["status"]="cancelled"
            raise
        except Exception as exc:
            manifest["status"]="failed"
            manifest['error'] = str(exc)
            (run / 'failure.json').write_text(json.dumps({'stage': manifest['workflow_stage'], 'error': str(exc)}, ensure_ascii=False), encoding='utf-8')
            raise
        finally:
            await self.stop(context.run_id)
            self.processes.pop(context.run_id,None)
            ACTIVE_RUNS.discard(context.run_id)
            manifest["finished_at_ns"] = time.time_ns()
            (run / "oaw.json").write_text(json.dumps(manifest),encoding="utf-8")
            await asyncio.to_thread(capture, run, artifacts=True)
