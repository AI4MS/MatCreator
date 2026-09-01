function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, stableValue(item)]),
    );
  }
  return value;
}

export function settingsValuesEqual(left, right) {
  return JSON.stringify(stableValue(left)) === JSON.stringify(stableValue(right));
}

export function buildSettingsSavePlan(previous, next) {
  if (!previous) return { changed: false, envValues: null, restartBackend: false, settingsBody: null };

  const settingsBody = {};
  if (!settingsValuesEqual(previous.extraSkills, next.extraSkills)) {
    settingsBody.planning = { extra_skills: next.extraSkills };
  }
  if (!settingsValuesEqual(previous.disabledSkills, next.disabledSkills)) {
    settingsBody.skills = { disabled: next.disabledSkills };
  }
  if (previous.username !== next.username) {
    settingsBody.user = { name: next.username };
  }
  if (previous.defaultWorkdir !== next.defaultWorkdir) {
    settingsBody.workspace = { default_workdir: next.defaultWorkdir };
  }

  const llmChanged = !settingsValuesEqual(previous.llm, next.llm);
  if (llmChanged) settingsBody.llm = next.llm;

  const envChanged = !settingsValuesEqual(previous.envValues, next.envValues);
  return {
    changed: Object.keys(settingsBody).length > 0 || envChanged,
    envValues: envChanged ? next.envValues : null,
    restartBackend: llmChanged || envChanged,
    settingsBody: Object.keys(settingsBody).length > 0 ? settingsBody : null,
  };
}
