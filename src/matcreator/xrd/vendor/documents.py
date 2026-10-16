"""Portable, immutable-source XRD inputs. Parsing never executes uploaded content."""
import base64
import hashlib
import math
import re

from pydantic import BaseModel, Field
from matcreator.xrd.compat import ResourceValidationError


class InputDocument(BaseModel):
    kind: str = ""
    filename: str = ""
    source_base64: str = ""
    sha256: str = ""
    text: str = ""
    metadata: dict = Field(default_factory=dict)
    points: list[list[float]] = Field(default_factory=list)
    peaks: list[dict] = Field(default_factory=list)
    reference_node_id: str = ""


def decode_source(raw):
    if not raw or b"\x00" in raw:
        raise ValueError("请选择文本导出文件；二进制 RAW/RASX 或加密 TXT 不可直接导入")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("无法解码文本，请导出为 UTF-8 或 GB18030")


def parse_pattern(text):
    points, metadata = [], {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("*"):
            match = re.match(r'\*(\S+)\s+"?(.*?)"?$', line)
            if match:
                metadata[match[1]] = match[2]
            continue
        if line.startswith(("#", ";")):
            continue
        fields = re.split(r"[,\s]+", line)
        try:
            pair = [float(v) for v in fields]
        except ValueError:
            if not points and re.fullmatch(r"(?i)(2[-_ ]?theta|two_theta|angle)[,\s]+(intensity|counts)", line):
                continue
            raise ValueError(f"实验谱含无法识别的行：{line[:80]}") from None
        if len(pair) != 2 or not all(math.isfinite(v) for v in pair):
            raise ValueError("实验谱须为有限数值的两列：2θ（度）、强度")
        if not 0 < pair[0] < 180 or pair[1] < 0 or (points and pair[0] <= points[-1][0]):
            raise ValueError("2θ 必须在 (0,180) 内严格递增，强度必须非负")
        points.append(pair)
    if len(points) < 20 or len(points) > 200000 or max((p[1] for p in points), default=0) <= 0:
        raise ValueError("实验谱须含 20–200000 个点及非零强度")
    metadata["format"] = "SmartLab RAS_RAW text" if metadata.get("FILE_TYPE") == "RAS_RAW" else "two-column text"
    return points, metadata


def parse_reference(text):
    metadata, peaks = {}, []
    code = re.search(r"PDF#([^:\s]+)", text)
    wavelength = re.search(r"Lambda\s*=\s*([\d.]+)", text)
    metadata["reference_code"] = code[1] if code else ""
    metadata["wavelength"] = float(wavelength[1]) if wavelength else None
    lines = [s.strip() for s in text.splitlines() if s.strip()]
    if code and len(lines) >= 3:
        metadata.update(name=lines[1], formula=lines[2])
    for line in lines:
        match = re.match(r"^([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+\(\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)\s*\)", line)
        if not match:
            continue
        angle, d, intensity = map(float, match.group(1, 2, 3))
        if not (0 < angle < 180 and d > 0 and 0 <= intensity <= 10000):
            raise ValueError("标准峰的角度、d 间距或强度无效")
        peaks.append({"two_theta": angle, "d": d, "intensity": intensity, "hkl": list(map(int, match.group(4, 5, 6)))})
    if not peaks or len(peaks) > 10000:
        raise ValueError("未找到标准峰表：需要 2-Theta、d、I 和 (h k l) 列的文本标准卡片")
    return peaks, metadata


def import_input(value, args):
    try:
        raw = base64.b64decode(args.get("source_base64", ""), validate=True)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("输入最大 8 MiB")
        text = decode_source(raw)
        kind = value["kind"]
        result = {**value, "filename": str(args.get("filename", "input.txt"))[:200],
                  "source_base64": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest(),
                  "text": text, "points": [], "peaks": [], "metadata": {}}
        if kind == "pattern":
            result["points"], result["metadata"] = parse_pattern(text)
        elif kind == "reference":
            result["peaks"], result["metadata"] = parse_reference(text)
        elif kind == "cif":
            if not re.search(r"(?m)^data_", text) or not all(k in text for k in ("_cell_length_a", "_cell_length_b", "_cell_length_c", "_atom_site_")):
                raise ValueError("CIF 缺少数据块、晶胞或原子位点；标准峰表不能代替 CIF")
        return result
    except (ValueError, TypeError) as exc:
        raise ResourceValidationError(str(exc)) from exc


def associate(value, args):
    if value["kind"] != "cif":
        raise ResourceValidationError("仅 CIF 对象可关联标准卡片")
    reference = args.get("reference_node_id", "")
    if not isinstance(reference, str) or len(reference) > 200:
        raise ResourceValidationError("无效的标准卡片 ID")
    return {**value, "reference_node_id": reference}
