import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Privacy } from "./Privacy";

describe("the privacy page", () => {
  it("names what is read, what is kept, who else handles it, and how it is deleted", () => {
    render(<Privacy />);
    for (const heading of ["What the app reads", "What it keeps", "Who else handles it", "When a store removes the app"]) {
      expect(screen.getByRole("heading", { name: heading })).toBeTruthy();
    }
    expect(screen.getByText(/never asks for customers, orders, payments or staff accounts/)).toBeTruthy();
    expect(screen.getByText(/48 hours later/)).toBeTruthy();
  });

  it("names no model vendor and links home relatively, for any base path", () => {
    const { container } = render(<Privacy />);
    expect(container.textContent).not.toMatch(/claude|anthropic/i);
    expect(container.querySelector(".wordmark")?.getAttribute("href")).toBe("../");
  });
});
