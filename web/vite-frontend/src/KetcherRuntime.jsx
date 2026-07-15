import React from "react";
import { createRoot } from "react-dom/client";
import { Editor } from "ketcher-react";
import { StandaloneStructServiceProvider } from "ketcher-standalone";
import "ketcher-react/dist/index.css";

const structServiceProvider = new StandaloneStructServiceProvider();

export function mountKetcherRuntime(
  target,
  { initialSmiles = "", onReady, onError } = {},
) {
  const root = createRoot(target);
  root.render(
    <Editor
      staticResourcesUrl="/"
      structServiceProvider={structServiceProvider}
      disableMacromoleculesEditor
      errorHandler={(message) => onError?.(String(message))}
      onInit={async (ketcher) => {
        try {
          if (initialSmiles) await ketcher.setMolecule(initialSmiles);
          onReady?.(ketcher);
        } catch (error) {
          onError?.(String(error?.message || error));
        }
      }}
    />,
  );
  return () => root.unmount();
}
