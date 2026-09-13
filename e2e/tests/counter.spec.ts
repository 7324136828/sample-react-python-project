import { expect, test } from '@playwright/test';

const backendUrl = 'http://127.0.0.1:8001';

test.beforeEach(async ({ request }) => {
  const response = await request.delete(`${backendUrl}/api/counter`);
  expect(response.ok()).toBeTruthy();
});

test('serves the frontend through the live FastAPI backend', async ({ page }) => {
  const healthResponse = await page.request.get('/api/health');

  expect(healthResponse.ok()).toBeTruthy();
  await expect(healthResponse.json()).resolves.toEqual({ status: 'ok' });

  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Persistent click counter' })).toBeVisible();
  await expect(page.locator('.count-value')).toHaveText('0');
});

test('increments, persists, and clears the counter end to end', async ({ page, request }) => {
  await page.goto('/');
  await expect(page.locator('.count-value')).toHaveText('0');

  await page.getByRole('button', { name: 'Record a click' }).click();
  await expect(page.locator('.count-value')).toHaveText('1');

  await page.reload();
  await expect(page.locator('.count-value')).toHaveText('1');

  const counterResponse = await request.get(`${backendUrl}/api/counter`);
  expect(counterResponse.ok()).toBeTruthy();
  expect((await counterResponse.json()).count).toBe(1);

  await page.getByRole('button', { name: 'Clear count' }).click();
  await expect(page.locator('.count-value')).toHaveText('0');
  await expect(page.getByRole('button', { name: 'Clear count' })).toBeDisabled();
});
