import { defineConfig } from '@playwright/test';
import path from 'node:path';
import base from './playwright.config';
export default defineConfig({
  ...base, testMatch: 'auth.spec.ts',
  webServer: {
    ...(base.webServer as object),
    env: { FRESHROUTE_DB_PATH: path.resolve('../work/browser-auth.sqlite3'), FRESHROUTE_OPERATOR_PASSWORD: 'test-only-workspace-password' },
  },
});
