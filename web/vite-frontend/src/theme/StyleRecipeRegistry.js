const STYLE_RECIPES = Object.freeze([
  Object.freeze({
    id: "standard",
    apiVersion: 1,
    name: "Standard",
    description: "Universal MatCreator component styling without a structural skin override.",
  }),
  Object.freeze({
    id: "rack-lab",
    apiVersion: 1,
    name: "Rack Lab",
    description: "Pre-reviewed instrument-panel structure, tactile controls, and restrained effects.",
  }),
]);

const recipeKey = (id, apiVersion) => `${id}@${apiVersion}`;
const recipesByKey = new Map(
  STYLE_RECIPES.map((recipe) => [recipeKey(recipe.id, recipe.apiVersion), recipe]),
);

/**
 * Read-only registry for CSS recipes that ship inside the MatCreator bundle.
 * There is intentionally no register(), loader, URL, or CSS-text API here.
 */
export const STYLE_RECIPE_REGISTRY = Object.freeze({
  get(id, apiVersion = 1) {
    return recipesByKey.get(recipeKey(id, apiVersion)) || null;
  },
  has(id, apiVersion = 1) {
    return recipesByKey.has(recipeKey(id, apiVersion));
  },
  list() {
    return [...STYLE_RECIPES];
  },
});
