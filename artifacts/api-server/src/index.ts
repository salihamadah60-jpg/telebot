import { spawn } from "child_process";
import * as path from "path";
import app from "./app";
import { logger } from "./lib/logger";

const rawPort = process.env["PORT"];

if (!rawPort) {
  throw new Error(
    "PORT environment variable is required but was not provided.",
  );
}

const port = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

app.listen(port, (err) => {
  if (err) {
    logger.error({ err }, "Error listening on port");
    process.exit(1);
  }

  logger.info({ port }, "Server listening");
  spawnBot();
});

function spawnBot() {
  const botDir = path.resolve(process.cwd(), "../../bot");

  logger.info({ botDir }, "Spawning Telegram bot");

  const bot = spawn("python3", ["main.py"], {
    cwd: botDir,
    env: { ...process.env },
    stdio: "inherit",
  });

  bot.on("close", (code) => {
    logger.warn({ code }, "Bot process exited, restarting in 10s...");
    setTimeout(spawnBot, 10_000);
  });

  bot.on("error", (err) => {
    logger.error({ err }, "Failed to start bot process");
    setTimeout(spawnBot, 10_000);
  });
}
