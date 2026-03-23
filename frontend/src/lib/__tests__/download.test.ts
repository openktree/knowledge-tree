import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { downloadJson } from "@/lib/download";

describe("downloadJson", () => {
  let createObjectURLSpy: ReturnType<typeof vi.fn>;
  let revokeObjectURLSpy: ReturnType<typeof vi.fn>;
  let clickSpy: ReturnType<typeof vi.fn>;
  let createElementSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    createObjectURLSpy = vi.fn().mockReturnValue("blob:mock-url");
    revokeObjectURLSpy = vi.fn();
    clickSpy = vi.fn();

    global.URL.createObjectURL = createObjectURLSpy as typeof URL.createObjectURL;
    global.URL.revokeObjectURL = revokeObjectURLSpy as typeof URL.revokeObjectURL;

    createElementSpy = vi.spyOn(document, "createElement").mockReturnValue({
      href: "",
      download: "",
      click: clickSpy,
    } as unknown as HTMLElement);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("creates a Blob from JSON data", () => {
    const data = { hello: "world" };
    downloadJson(data, "test.json");

    expect(createObjectURLSpy).toHaveBeenCalledOnce();
    const blob = createObjectURLSpy.mock.calls[0][0] as Blob;
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe("application/json");
  });

  it("sets download attribute to the provided filename", () => {
    downloadJson({ a: 1 }, "my-export.json");

    const anchor = createElementSpy.mock.results[0].value;
    expect(anchor.download).toBe("my-export.json");
  });

  it("clicks the anchor to trigger download", () => {
    downloadJson({}, "file.json");
    expect(clickSpy).toHaveBeenCalledOnce();
  });

  it("revokes the object URL after download", () => {
    downloadJson({}, "file.json");
    expect(revokeObjectURLSpy).toHaveBeenCalledWith("blob:mock-url");
  });

  it("creates anchor element via document.createElement", () => {
    downloadJson({}, "file.json");
    expect(createElementSpy).toHaveBeenCalledWith("a");
  });
});
