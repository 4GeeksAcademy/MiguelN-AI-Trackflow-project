/**
 * `lib/auth-storage.ts`: where the session token lives, and where a user is
 * sent after signing in.
 *
 * `sanitizeNextPath` comes first: `/login?next=...` is a link anyone can craft,
 * and whatever survives it is where a freshly authenticated user lands.
 */
import {
  buildLoginAfterRegisterUrl,
  buildLoginUrl,
  clearToken,
  handleUnauthorized,
  hasRegisteredFlag,
  hasResetSuccessFlag,
  readResetToken,
  readToken,
  sanitizeNextPath,
  storeToken,
  TOKEN_STORAGE_KEY,
  UNAUTHORIZED_EVENT,
} from "@/lib/auth-storage";

import { installBrowser, removeBrowser } from "./support/browser";

const SITE = "https://app.trackflow.com";

afterEach(() => {
  clearToken();
  removeBrowser();
});

describe("sanitizeNextPath", () => {
  it.each(["/suppliers", "/suppliers?country=Spain&category=carrier", "/incidents#summary", "/account/profile"])(
    "keeps the same-site path %s",
    (path) => {
      expect(sanitizeNextPath(path)).toBe(path);
    },
  );

  it.each([
    ["a backslash (BUG-2)", "/\\evil.com"],
    ["a tab (BUG-2)", "/\t/evil.com"],
    ["a newline (BUG-2)", "/\n/evil.com"],
    ["a carriage return", "/\r/evil.com"],
  ])("refuses a path that browsers resolve off-site through %s", (_label, value) => {
    // Browsers read `\` as `/` and drop tabs and newlines while parsing, so
    // each of these becomes `//evil.com` - another origin - once navigated to.
    expect(sanitizeNextPath(value)).toBeNull();
  });

  it("refuses any other control character, which has no place in a path", () => {
    expect(sanitizeNextPath("/suppliers\u0000")).toBeNull();
    expect(sanitizeNextPath("/suppliers\u007f")).toBeNull();
  });

  it.each([
    ["protocol-relative", "//evil.com"],
    ["absolute", "https://evil.com/login"],
    ["javascript:", "javascript:alert(1)"],
    ["relative", "suppliers"],
    ["empty", ""],
    ["missing", null],
    ["undefined", undefined],
  ])("refuses a %s value", (_label, value) => {
    expect(sanitizeNextPath(value)).toBeNull();
  });

  it.each([
    "/\\evil.com",
    "/\t/evil.com",
    "//evil.com",
    "///evil.com",
    "/..//evil.com",
    "/.//evil.com",
    "/%2F%2Fevil.com",
    "/@evil.com",
    "/suppliers",
  ])("never lets %j land off-site", (value) => {
    const kept = sanitizeNextPath(value);

    if (kept !== null) {
      expect(new URL(kept, SITE).origin).toBe(SITE);
    }
  });

  it.each(["/login", "/login?next=/suppliers", "/login/", "/login#top", "/suppliers/../login"])(
    "refuses the sign-in page itself, which would loop: %s",
    (value) => {
      expect(sanitizeNextPath(value)).toBeNull();
    },
  );

  it("keeps /LOGIN: Next.js paths are case-sensitive, so it is not the sign-in page (pinned)", () => {
    expect(sanitizeNextPath("/LOGIN")).toBe("/LOGIN");
  });
});

describe("buildLoginUrl", () => {
  it("carries a safe next path, encoded", () => {
    expect(buildLoginUrl("/suppliers?country=Spain")).toBe("/login?next=%2Fsuppliers%3Fcountry%3DSpain");
  });

  it("drops an unsafe next path instead of passing it along", () => {
    expect(buildLoginUrl("/\\evil.com")).toBe("/login");
    expect(buildLoginUrl(null)).toBe("/login");
  });
});

describe("session token storage", () => {
  it("stores, reads back and clears the token", () => {
    const browser = installBrowser();

    storeToken("header.payload.signature");
    expect(browser.storage?.getItem(TOKEN_STORAGE_KEY)).toBe("header.payload.signature");
    expect(readToken()).toBe("header.payload.signature");

    clearToken();
    expect(readToken()).toBeNull();
  });

  it("treats a blank stored token as no session", () => {
    const browser = installBrowser();
    browser.storage?.setItem(TOKEN_STORAGE_KEY, "   ");

    expect(readToken()).toBeNull();
  });

  it("keeps the session in memory for this page load when storage is blocked", () => {
    installBrowser({ storage: "blocked" });

    storeToken("header.payload.signature");
    expect(readToken()).toBe("header.payload.signature");

    clearToken();
    expect(readToken()).toBeNull();
  });

  it("does nothing during a server render, where there is no window", () => {
    expect(() => storeToken("header.payload.signature")).not.toThrow();
    expect(readToken()).toBeNull();
    expect(() => clearToken()).not.toThrow();
  });
});

describe("handleUnauthorized", () => {
  it("ends the session and asks the app to send the user to sign in", () => {
    const browser = installBrowser();
    storeToken("header.payload.signature");

    handleUnauthorized();

    expect(readToken()).toBeNull();
    expect(browser.events).toEqual([UNAUTHORIZED_EVENT]);
  });

  it("does not throw during a server render", () => {
    expect(() => handleUnauthorized()).not.toThrow();
  });
});

describe("readResetToken", () => {
  it("reads the token from the emailed link", () => {
    expect(readResetToken("?token=header.payload.signature")).toBe("header.payload.signature");
  });

  it.each(["", "?token=", "?token=%20%20", "?other=1"])("finds no token in %j", (search) => {
    expect(readResetToken(search)).toBeNull();
  });
});

describe("hasResetSuccessFlag", () => {
  it("recognises the redirect out of a successful reset", () => {
    expect(hasResetSuccessFlag("?reset=success")).toBe(true);
  });

  it.each(["", "?reset=failed", "?reset=SUCCESS"])("ignores %j", (search) => {
    expect(hasResetSuccessFlag(search)).toBe(false);
  });
});

describe("hasRegisteredFlag", () => {
  it("recognises the redirect out of a registration", () => {
    expect(hasRegisteredFlag("?registered=1&next=%2F")).toBe(true);
  });

  it.each(["", "?registered=true", "?registered=0"])("ignores %j", (search) => {
    expect(hasRegisteredFlag(search)).toBe(false);
  });
});

describe("buildLoginAfterRegisterUrl", () => {
  it("flags the registration and carries the next path", () => {
    const url = new URL(buildLoginAfterRegisterUrl("/suppliers"), SITE);

    expect(url.pathname).toBe("/login");
    expect(url.searchParams.get("registered")).toBe("1");
    expect(url.searchParams.get("next")).toBe("/suppliers");
  });

  it("keeps a crafted next path encoded, so it cannot add parameters of its own", () => {
    const url = new URL(buildLoginAfterRegisterUrl("/x?registered=0&next=//evil.com"), SITE);

    expect(url.searchParams.getAll("registered")).toEqual(["1"]);
    expect(url.searchParams.getAll("next")).toEqual(["/x?registered=0&next=//evil.com"]);
  });
});
