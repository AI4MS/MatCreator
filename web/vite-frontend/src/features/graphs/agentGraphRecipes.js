// Canvas graph treatments are recipe capabilities, not skin-ID branches.
// A skin can reuse a reviewed recipe with different tokens, while adding or
// removing a recipe remains a self-contained bundled source change.
const STANDARD_AGENT_GRAPH_RECIPE = Object.freeze({
  id: "standard",
  apiVersion: 1,
  nodeShape: "circle",
  liquid: null,
});

const RACK_LAB_AGENT_GRAPH_RECIPE = Object.freeze({
  id: "rack-lab",
  apiVersion: 1,
  nodeShape: "droplet",
  liquid: Object.freeze({
    defaultFillAlpha: 0.46,
    underlayAlpha: 0.24,
    highlightAlpha: 0.28,
    sheenAlpha: 0.1,
    rimAlpha: 0.86,
  }),
});

const RECIPES = Object.freeze([
  STANDARD_AGENT_GRAPH_RECIPE,
  RACK_LAB_AGENT_GRAPH_RECIPE,
]);

const recipesByKey = new Map(
  RECIPES.map((recipe) => [`${recipe.id}@${recipe.apiVersion}`, recipe]),
);

export function resolveAgentGraphRecipe(id, apiVersion = 1) {
  return recipesByKey.get(`${id}@${apiVersion}`) || STANDARD_AGENT_GRAPH_RECIPE;
}

export function listAgentGraphRecipes() {
  return [...RECIPES];
}
