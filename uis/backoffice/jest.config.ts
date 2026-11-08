import type { Config } from "jest";
import nextJest from "next/jest.js";

// Jest for this app's authentication logic (AUTH-088). Run with `npm test`.
//
// The test files live in tests/frontend at the repository root. `shared/` runs
// against this app's copy of the auth modules and against uis/website's, so
// the two copies cannot drift apart unnoticed; `backoffice/` covers what only
// this app has.
const createJestConfig = nextJest({ dir: import.meta.dirname });

const config: Config = {
  displayName: "backoffice",
  testEnvironment: "node",
  roots: ["<rootDir>/../../tests/frontend/shared", "<rootDir>/../../tests/frontend/backoffice"],
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/$1",
    // The client this app reaches the identity API through, so the shared
    // tests can build the same error classes the code under test checks for.
    "^@identity-api-client$": "<rootDir>/lib/api-client",
  },
  setupFiles: ["<rootDir>/../../tests/frontend/setup-env.js"],
  collectCoverageFrom: [
    "lib/auth.ts",
    "lib/auth-storage.ts",
    "lib/api-client.ts",
    "lib/friendly-error.ts",
    "lib/inventory.ts",
    "services/auth-service.ts",
  ],
};

export default createJestConfig(config);
