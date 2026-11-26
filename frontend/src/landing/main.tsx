// Inter, for every device without Apple's system face. This is the build with the optical-size axis, so large
// headlines get its display cut and body text its text cut.
import "@fontsource-variable/inter/opsz.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Landing } from "./Landing";
import "./landing.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Landing />
  </StrictMode>,
);
