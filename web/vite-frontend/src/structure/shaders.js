import * as THREE from "three";

export const glossyVertex = `
varying vec3 vNormal;
varying vec3 vViewDir;
varying vec3 vBaseColor;
uniform vec3 uColor;
void main() {
  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mvPosition;
  vNormal = normalize(normalMatrix * normal);
  vViewDir = normalize(-mvPosition.xyz);
  vBaseColor = uColor;
}
`;

// AtomClay's glossy look: broad studio highlights, soft diffuse response,
// view-angle Fresnel, and a small ambient occlusion term. This is deliberately
// self-contained so the viewer does not depend on a materials package.
export const glossyFragment = `
precision highp float;
varying vec3 vNormal;
varying vec3 vViewDir;
uniform vec3 uColor;
varying vec3 vBaseColor;
const int AREA_LIGHT_COUNT = 3;
uniform vec3 uAreaLightDirs[AREA_LIGHT_COUNT];
uniform vec3 uAreaLightColors[AREA_LIGHT_COUNT];
uniform vec3 uAmbientColor;
uniform vec3 uSpecularColor;
uniform float uShininess;
uniform float uSpecularStrength;
uniform vec2 uRectSize;
uniform float uRectSoftness;
uniform float uAOIntensity;
uniform float uFresnelStrength;
uniform vec3 uSelectionEmissive;
uniform float uSelectionFactor;
void main() {
  vec3 normal = normalize(vNormal);
  vec3 viewDir = normalize(vViewDir);
  float ao = mix(1.0, 1.0 - uAOIntensity, pow(1.0 - max(dot(normal, viewDir), 0.0), 2.2));
  float diffuseAccum = 0.0;
  vec3 specAccum = vec3(0.0);
  for (int i = 0; i < AREA_LIGHT_COUNT; i++) {
    vec3 lightDir = normalize(uAreaLightDirs[i]);
    diffuseAccum += max(dot(normal, lightDir), 0.0);
    vec3 reflected = reflect(-viewDir, normal);
    float spec = pow(max(dot(reflected, lightDir), 0.0), uShininess);
    vec3 up = abs(lightDir.z) < 0.95 ? vec3(0.0, 0.0, 1.0) : vec3(0.0, 1.0, 0.0);
    vec3 right = normalize(cross(up, lightDir));
    vec3 lightUp = normalize(cross(lightDir, right));
    vec3 reflectedProjection = normalize(reflected - lightDir * dot(reflected, lightDir) + vec3(1e-5));
    float rx = abs(dot(reflectedProjection, right)) / max(0.0001, uRectSize.x);
    float ry = abs(dot(reflectedProjection, lightUp)) / max(0.0001, uRectSize.y);
    float rectangle = 1.0 - smoothstep(1.0 - uRectSoftness, 1.0 + uRectSoftness, max(rx, ry));
    specAccum += uAreaLightColors[i] * spec * rectangle;
  }
  diffuseAccum /= float(AREA_LIGHT_COUNT);
  vec3 ambient = vBaseColor * (uAmbientColor * 1.6) * ao;
  vec3 diffuse = vBaseColor * (0.35 + diffuseAccum * 0.6);
  float fresnel = pow(1.0 - max(dot(normal, viewDir), 0.0), 5.0);
  vec3 specular = (uSpecularColor * specAccum * uSpecularStrength) + (uSpecularColor * fresnel * uFresnelStrength);
  vec3 finalColor = ambient + diffuse + specular;
  finalColor = mix(finalColor, finalColor + uSelectionEmissive, uSelectionFactor);
  gl_FragColor = vec4(finalColor, 1.0);
}
`;

export const glossyUniforms = (color) => ({
  uColor: { value: color },
  uAreaLightDirs: { value: [new THREE.Vector3(.7, .2, 1), new THREE.Vector3(-.4, .6, .9), new THREE.Vector3(.2, -.7, .8)].map((light) => light.normalize()) },
  uAreaLightColors: { value: [new THREE.Color(1, 1, 1), new THREE.Color(.95, .98, 1), new THREE.Color(1, .97, .9)] },
  uAmbientColor: { value: new THREE.Color(.12, .12, .12) },
  uSpecularColor: { value: new THREE.Color(1, 1, 1) },
  uShininess: { value: 96 },
  uSpecularStrength: { value: 1.7 },
  uRectSize: { value: new THREE.Vector2(.32, .2) },
  uRectSoftness: { value: .12 },
  uAOIntensity: { value: .1 },
  uFresnelStrength: { value: .6 },
  uSelectionEmissive: { value: new THREE.Color(.24, .6, 1) },
  uSelectionFactor: { value: 0 },
});
