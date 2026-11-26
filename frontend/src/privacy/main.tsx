// Inter, as on the landing page: for every device without Apple's system face, with the optical-size axis.
import "@fontsource-variable/inter/opsz.css";
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
