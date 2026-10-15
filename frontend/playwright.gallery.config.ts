import { defineConfig } from '@playwright/test';
import path from 'node:path';
const python = path.resolve(process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python');
export default defineConfig({
  testDir: './tests/gallery', timeout: 240_000, workers: 1,
  use: { baseURL: 'http://127.0.0.1:8011', headless: true, viewport: { width: 1920, height: 1080 }, reducedMotion: 'reduce' },
  webServer: {
    command: `"${python}" -m uvicorn app.main:app --app-dir ../backend --host 127.0.0.1 --port 8011`,
    url: 'http://127.0.0.1:8011/health', timeout: 30_000, reuseExistingServer: false,
    env: { FRESHROUTE_DB_PATH: path.resolve('../work/gallery-capture.sqlite3'), FRESHROUTE_OPERATOR_PASSWORD: '', FRESHROUTE_SECURE_COOKIES: '' },
  },
});
