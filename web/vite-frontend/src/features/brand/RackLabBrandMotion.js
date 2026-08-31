import { animate, splitText, stagger } from "animejs";

const RACK_LAB_RECIPE = "rack-lab";
const RACK_LAB_RECIPE_VERSION = "1";

export function createRackLabBrandMotion({
  documentRef = document,
  windowRef = window,
  animateImpl = animate,
  splitTextImpl = splitText,
  staggerImpl = stagger,
} = {}) {
  const target = documentRef.querySelector?.(".brand-wordmark-base");
  const reducedMotion = windowRef.matchMedia?.("(prefers-reduced-motion: reduce)");
  let splitter = null;
  let animation = null;

  function stop() {
    animation?.cancel?.();
    animation = null;
    splitter?.revert?.();
    splitter = null;
  }

  function isRackLabActive() {
    const dataset = documentRef.body?.dataset || {};
    return dataset.styleRecipe === RACK_LAB_RECIPE
      && dataset.styleRecipeVersion === RACK_LAB_RECIPE_VERSION;
  }

  function sync() {
    const shouldRun = Boolean(target) && isRackLabActive() && !reducedMotion?.matches;
    if (!shouldRun) {
      stop();
      return;
    }
    if (splitter) return;

    splitter = splitTextImpl(target, {
      chars: { class: "rack-brand-char" },
      accessible: false,
    });
    animation = animateImpl(splitter.chars, {
      y: ["0em", "-0.22em", "0em"],
      scale: [1, 1.025, 1],
      duration: 900,
      delay: staggerImpl(65),
      ease: "inOut(2)",
      loop: true,
      loopDelay: 1450,
    });
  }

  const handleThemeChange = () => sync();
  const handleMotionPreference = () => sync();
  windowRef.addEventListener?.("matcreator-theme-change", handleThemeChange);
  reducedMotion?.addEventListener?.("change", handleMotionPreference);
  sync();

  return {
    sync,
    destroy() {
      windowRef.removeEventListener?.("matcreator-theme-change", handleThemeChange);
      reducedMotion?.removeEventListener?.("change", handleMotionPreference);
      stop();
    },
  };
}
