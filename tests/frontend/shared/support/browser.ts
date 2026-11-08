/**
 * Just enough of a browser for the auth modules: a `window` with `localStorage`
 * and events.
 *
 * The suite runs in Node, where `window` does not exist - which is exactly the
 * server-render case the modules must survive. Tests that need a browser
 * install one and remove it afterwards.
 */

/** An in-memory `localStorage`. */
class MemoryStorage {
  private readonly items = new Map<string, string>();

  getItem(key: string): string | null {
    return this.items.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.items.set(key, String(value));
  }

  removeItem(key: string): void {
    this.items.delete(key);
  }
}

export type FakeBrowser = {
  /** `null` when storage is blocked. */
  storage: MemoryStorage | null;
  /** The type of every event dispatched on `window`, in order. */
  events: string[];
};

/**
 * Put a `window` on the global object.
 *
 * `storage: "blocked"` mimics private mode or blocked site data: merely
 * touching `window.localStorage` throws, as it does in real browsers.
 */
export function installBrowser({ storage = "available" }: { storage?: "available" | "blocked" } = {}): FakeBrowser {
  const target = new EventTarget();
  const events: string[] = [];
  const memory = storage === "available" ? new MemoryStorage() : null;

  const fakeWindow = {
    addEventListener: target.addEventListener.bind(target),
    removeEventListener: target.removeEventListener.bind(target),
    dispatchEvent(event: Event): boolean {
      events.push(event.type);
      return target.dispatchEvent(event);
    },
  };

  Object.defineProperty(fakeWindow, "localStorage", {
    get() {
      if (!memory) {
        throw new DOMException("The operation is insecure.", "SecurityError");
      }
      return memory;
    },
  });

  Object.defineProperty(globalThis, "window", { value: fakeWindow, configurable: true, writable: true });

  return { storage: memory, events };
}

/** Back to a server render: no `window` at all. */
export function removeBrowser(): void {
  Reflect.deleteProperty(globalThis, "window");
}
