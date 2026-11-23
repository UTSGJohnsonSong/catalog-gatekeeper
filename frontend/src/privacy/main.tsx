import "@fontsource-variable/familjen-grotesk";
import "@fontsource-variable/newsreader";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../landing/landing.css";
import { Privacy } from "./Privacy";
import "./privacy.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Privacy />
  </StrictMode>,
);
