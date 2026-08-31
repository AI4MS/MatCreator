const DEFAULT_MIN_SCALE = 0.15;
const DEFAULT_MAX_SCALE = 4;
const DEFAULT_ZOOM_FACTOR = 0.001;
const MAX_WHEEL_DELTA = 100;

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function normalizedWheelDelta(event, container) {
  // WheelEvent values can be expressed in pixels, lines, or pages. Converting
  // non-pixel values gives a physical wheel gesture a comparable effect across
  // browsers and input devices.
  if (event.deltaMode === 1) return event.deltaY * 16;
  if (event.deltaMode === 2) return event.deltaY * Math.max(container.clientHeight, 1);
  return event.deltaY;
}

/**
 * Install predictable, cursor-centred wheel zoom for a vis-network instance.
 *
 * vis-network's built-in handler applies a fixed scale step per WheelEvent.
 * High-resolution mice and trackpads may emit many events for one gesture,
 * which makes that behaviour depend on the input device rather than distance.
 */
export function installNetworkWheelZoom(container, network, {
  minScale = DEFAULT_MIN_SCALE,
  maxScale = DEFAULT_MAX_SCALE,
  zoomFactor = DEFAULT_ZOOM_FACTOR,
  onBeforeZoom = null,
} = {}) {
  if (!container || !network) return () => {};

  const onWheel = (event) => {
    if (event.deltaY === 0) return;
    if (event.cancelable) event.preventDefault();

    // Scale by distance and cap malformed events to a normal wheel notch.
    const delta = clamp(normalizedWheelDelta(event, container), -MAX_WHEEL_DELTA, MAX_WHEEL_DELTA);
    const oldScale = network.getScale();
    const newScale = clamp(
      oldScale * Math.exp(-delta * zoomFactor),
      minScale,
      maxScale,
    );
    if (newScale === oldScale) return;

    const bounds = container.getBoundingClientRect();
    const pointer = network.DOMtoCanvas({
      x: event.clientX - bounds.left,
      y: event.clientY - bounds.top,
    });
    const position = network.getViewPosition();
    const scaleRatio = oldScale / newScale;
    onBeforeZoom?.({ event, oldScale, newScale });
    network.moveTo({
      position: {
        x: pointer.x - (pointer.x - position.x) * scaleRatio,
        y: pointer.y - (pointer.y - position.y) * scaleRatio,
      },
      scale: newScale,
      animation: false,
    });
  };

  container.addEventListener("wheel", onWheel, { passive: false });
  return () => container.removeEventListener("wheel", onWheel);
}
