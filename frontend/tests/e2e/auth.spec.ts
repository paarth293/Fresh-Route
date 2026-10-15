import { test, expect } from '@playwright/test';

test('password-protected workspace supports login, planning and logout', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Sign in to the workspace' })).toBeVisible();
  expect((await page.request.get('/api/v1/demo-scenarios')).status()).toBe(401);
  await page.getByLabel('Workspace password').fill('incorrect');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Incorrect workspace password');
  await page.getByLabel('Workspace password').fill('test-only-workspace-password');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Generate plan', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Generate plan', exact: true }).click();
  await expect(page.getByText('Plan generated and independently validated.')).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Sign in to the workspace' })).toBeVisible();
  expect((await page.request.get('/api/v1/demo-scenarios')).status()).toBe(401);
});
