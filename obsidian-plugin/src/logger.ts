export type LogLevel = "debug" | "info" | "warn" | "error";

const LEVEL_ORDER: Record<LogLevel, number> = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
};

/**
 * Prefixed console logger shared across the plugin, so log lines are easy to
 * filter in Obsidian's developer console (Ctrl+Shift+I).
 */
export class Logger {
  private readonly prefix: string;
  private level: LogLevel;

  constructor(scope: string, level: LogLevel = "info") {
    this.prefix = `[${scope}]`;
    this.level = level;
  }

  setLevel(level: LogLevel): void {
    this.level = level;
  }

  debug(message: string, ...args: unknown[]): void {
    this.log("debug", message, args);
  }

  info(message: string, ...args: unknown[]): void {
    this.log("info", message, args);
  }

  warn(message: string, ...args: unknown[]): void {
    this.log("warn", message, args);
  }

  error(message: string, ...args: unknown[]): void {
    this.log("error", message, args);
  }

  private log(level: LogLevel, message: string, args: unknown[]): void {
    if (LEVEL_ORDER[level] < LEVEL_ORDER[this.level]) return;
    const line = `${this.prefix} ${message}`;
    switch (level) {
      case "debug":
        console.debug(line, ...args);
        break;
      case "info":
        console.info(line, ...args);
        break;
      case "warn":
        console.warn(line, ...args);
        break;
      case "error":
        console.error(line, ...args);
        break;
    }
  }
}
