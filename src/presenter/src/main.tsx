import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

function App() {
  return (
    <main>
      <h1>Close-out Presenter</h1>
      <p>Development foundation only. No live or stub integrations are connected.</p>
    </main>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("Missing Presenter root element");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
