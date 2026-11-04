import "@fontsource-variable/familjen-grotesk";
import "@fontsource-variable/newsreader";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Landing } from "./Landing";
import "./landing.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Landing />
  </StrictMode>,
);
