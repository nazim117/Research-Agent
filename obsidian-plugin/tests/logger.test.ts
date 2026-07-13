import { Logger } from "../src/logger";

describe("Logger", () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("prefixes messages with the given scope", () => {
    const spy = jest.spyOn(console, "info").mockImplementation(() => {});
    const logger = new Logger("TestScope");
    logger.info("hello");
    expect(spy).toHaveBeenCalledWith("[TestScope] hello");
  });

  it("suppresses messages below the configured level", () => {
    const spy = jest.spyOn(console, "debug").mockImplementation(() => {});
    const logger = new Logger("TestScope", "info");
    logger.debug("should not appear");
    expect(spy).not.toHaveBeenCalled();
  });

  it("respects level changes at runtime", () => {
    const spy = jest.spyOn(console, "debug").mockImplementation(() => {});
    const logger = new Logger("TestScope", "info");
    logger.setLevel("debug");
    logger.debug("now visible");
    expect(spy).toHaveBeenCalledWith("[TestScope] now visible");
  });

  it("always logs errors regardless of configured level", () => {
    const spy = jest.spyOn(console, "error").mockImplementation(() => {});
    const logger = new Logger("TestScope", "error");
    logger.error("boom");
    expect(spy).toHaveBeenCalledWith("[TestScope] boom");
  });

  it("forwards extra arguments to the underlying console call", () => {
    const spy = jest.spyOn(console, "warn").mockImplementation(() => {});
    const logger = new Logger("TestScope");
    const detail = { code: 42 };
    logger.warn("careful", detail);
    expect(spy).toHaveBeenCalledWith("[TestScope] careful", detail);
  });
});
