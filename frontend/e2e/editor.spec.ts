import { expect, test } from '@playwright/test';

test('semantic edit, undo, redo, autosave and refresh recovery', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('ScoresheetReader')).toBeVisible();
  await expect(page.getByText('本机 · Mock 识别')).toBeVisible();

  await page.locator('[data-field-id="header.competition"]').last().click();
  const competition = page.locator('label:has-text("竞赛名称") input');
  await competition.fill('浏览器恢复测试');
  await expect(page.locator('.save-indicator')).toHaveText(/等待保存|正在保存|已保存/);

  await page.getByRole('button', { name: '撤销' }).click();
  await expect(competition).toHaveValue('合成测试赛');
  await page.getByRole('button', { name: '重做' }).click();
  await expect(competition).toHaveValue('浏览器恢复测试');
  await expect(page.getByText('已保存')).toBeVisible({ timeout: 4_000 });

  await page.reload();
  await expect(page.locator('label:has-text("竞赛名称") input')).toHaveValue('浏览器恢复测试');
});

test('game prior, mock recognition, editable import and selective rerun merge', async ({ page }) => {
  const onePixelPng = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
    'base64',
  );
  await page.goto('/');
  await page.getByRole('button', { name: /选择比赛/ }).click();
  const dialog = page.getByRole('dialog', { name: '选择比赛' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: /示例学院甲.*示例学院乙/ }).click();
  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'),
    dialog.getByRole('button', { name: /上传这场比赛的照片/ }).click(),
  ]);
  await chooser.setFiles({
    name: 'public-demo-sheet.png',
    mimeType: 'image/png',
    buffer: onePixelPng,
  });
  await expect(dialog).toHaveCount(0);

  await page.locator('rect[data-field-id="header.competition"]').dblclick();
  await expect(page.getByLabel('竞赛名称')).toHaveValue('公开合成测试赛');
  await expect(page.getByLabel('竞赛名称')).toBeDisabled();
  await expect(page.getByLabel('比赛序号')).toHaveValue('');

  await page.getByRole('button', { name: /整图识别/ }).click();
  await expect(page.getByRole('button', { name: /重新识别/ })).toBeVisible({ timeout: 8_000 });
  await expect(page.getByLabel('大模型识别结果')).toContainText('总计 0 tokens');
  await expect(page.getByLabel('识别到的记录台人员')).toContainText('示例记录台人员甲');
  await page.locator('rect[data-field-id="officials"]').click({ force: true });
  const firstTablePerson = page.getByRole('textbox', { name: '记录台人员 1', exact: true });
  await expect(firstTablePerson).toHaveValue('示例记录台人员甲');
  await expect(page.getByRole('textbox', { name: '记录员姓名', exact: true })).toHaveValue('');
  await firstTablePerson.fill('人工核对人员');
  await expect(page.locator('.save-indicator')).toHaveText('已保存', { timeout: 4_000 });
  await page.screenshot({ path: '../output/playwright/recognition-applied.png', fullPage: true });

  await page.getByRole('button', { name: /选择比赛/ }).click();
  const recognizedDialog = page.getByRole('dialog', { name: '选择比赛' });
  const recognizedGame = recognizedDialog.getByRole('button', {
    name: /示例学院甲.*示例学院乙.*已识别/,
  });
  await expect(recognizedGame).toBeVisible();
  await recognizedGame.click();
  await expect(recognizedDialog).toHaveCount(0);
  await expect(page.getByLabel('大模型识别结果')).toContainText('总计 0 tokens');

  await page.locator('rect[data-field-id="team.A.player.01.name"]').dblclick();
  await expect(page.getByLabel('姓名')).toHaveValue('甲队员一');

  await page.locator('rect[data-field-id="team.A.head_coach"]').click({ position: { x: 20, y: 5 } });
  const coach = page.getByLabel('教练员', { exact: true });
  await expect(coach).toHaveValue('示例教练');
  await coach.fill('人工修改教练');
  await page.getByRole('button', { name: '撤销' }).click();
  await expect(coach).toHaveValue('示例教练');
  await page.getByRole('button', { name: '重做' }).click();
  await expect(coach).toHaveValue('人工修改教练');
  await expect(page.locator('.save-indicator')).toHaveText('已保存', { timeout: 4_000 });
  await page.reload();
  await page.locator('rect[data-field-id="team.A.head_coach"]').click({ position: { x: 20, y: 5 } });
  await expect(page.getByLabel('教练员', { exact: true })).toHaveValue('人工修改教练');

  await page.locator('rect[data-field-id="summary.ended_at"]').dblclick();
  await page.getByLabel('比赛结束时间').fill('16:00');
  await expect(page.locator('.save-indicator')).toHaveText('已保存', { timeout: 4_000 });
  await page.getByRole('button', { name: /重新识别/ }).click();
  await expect(page.getByText('选择要应用的区域')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByLabel('大模型识别结果')).toContainText('缓存命中');
  await page.screenshot({ path: '../output/playwright/recognition-diff.png', fullPage: true });
  await page.getByText('节比分与比赛结果', { exact: true }).click();
  await page.getByText('记录台人员与裁判', { exact: true }).click();
  await page.getByRole('button', { name: /应用所选/ }).click();

  await page.locator('rect[data-field-id="team.A.head_coach"]').click({ position: { x: 20, y: 5 } });
  await expect(page.getByLabel('教练员', { exact: true })).toHaveValue('示例教练');
  await page.locator('rect[data-field-id="summary.ended_at"]').dblclick();
  await expect(page.getByLabel('比赛结束时间')).toHaveValue('16:00');
  await page.locator('rect[data-field-id="officials"]').click({ force: true });
  await expect(page.getByRole('textbox', { name: '记录台人员 1', exact: true }))
    .toHaveValue('人工核对人员');
});

test('validation issues jump back to the related template region', async ({ page }) => {
  await page.goto('/');
  await page.locator('[data-field-id="summary"]').click();
  await page.locator('label:has-text("A 队最终比分") input').fill('99');
  await page.locator('[data-field-id="header.competition"]').last().click();
  await page.getByRole('button', { name: /校验/ }).click();

  const issue = page.getByRole('button', { name: /FINAL_SCORE_MISMATCH/ });
  await expect(issue).toBeVisible();
  await issue.click();
  await expect(page.locator('[data-field-id="summary"]')).toHaveClass(/is-selected/);
});

test('running-score ledger supports reconciliation, insertion, editing and deletion', async ({ page }) => {
  await page.goto('/');
  await page.locator('rect[data-field-id="score.A.008"]').dblclick();

  const ledger = page.getByLabel('A 队得分事件账本');
  await expect(ledger).toBeVisible();
  await expect(page.getByRole('tab', { name: /Q2/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('[data-score-field="score.A.008"]')).toHaveClass(/is-targeted/);
  await expect(page.getByRole('button', { name: '删除累计 2 分事件' })).toHaveCount(0);
  await expect(page.getByLabel('A 队节比分核对')).toContainText('Q1');
  await expect(page.getByRole('button', { name: '在累计 8 分之前插入' })).toBeVisible();
  await page.getByRole('button', { name: '在累计 8 分之前插入' }).click();
  await expect(page.getByRole('button', { name: '删除累计 8 分事件' })).toBeVisible();

  await page.getByLabel('本次得分', { exact: true }).selectOption('3');
  await expect(page.getByRole('button', { name: '删除累计 9 分事件' })).toBeVisible();
  await expect(page.locator('[data-field-id="score.A.024.scorer"]')).toHaveCount(1);
  await page.screenshot({ path: '../output/playwright/score-event-ledger.png', fullPage: true });

  await page.getByRole('button', { name: '删除累计 9 分事件' }).click();
  await expect(page.locator('[data-field-id="score.A.021.scorer"]')).toHaveCount(1);
  await expect(page.locator('[data-field-id="score.A.024.scorer"]')).toHaveCount(0);

  const playerName = page.locator('text[data-field-id="team.A.player.01.name"]');
  await expect(playerName).toHaveAttribute('font-size', '7.1');
  await expect.poll(() => playerName.evaluate((element) => getComputedStyle(element).stroke))
    .toBe('none');
});

test('selection protocol, collapsible panels, and persistent splitters work together', async ({ page }) => {
  await page.goto('/');
  const block = page.locator('rect[data-field-id="team.A.meta"][data-selection-level="block"]');
  const detail = page.locator(
    'rect[data-field-id="team.A.team_foul.2.3"][data-selection-level="detail"]',
  );

  await detail.click();
  await expect(block).toHaveClass(/is-selected/);

  await detail.dblclick();
  await expect(detail).toHaveClass(/is-selected/);
  await expect(page.locator('.team-foul-controls input').nth(1)).toBeFocused();

  await page.getByRole('button', { name: '收起原图面板' }).click();
  await expect(page.getByRole('region', { name: '原始照片' })).toHaveCount(0);
  await page.getByRole('button', { name: '展开原图面板' }).click();
  const sourcePane = page.getByRole('region', { name: '原始照片' });
  await expect(sourcePane).toBeVisible();

  const sourceDivider = page.getByRole('separator', { name: '调整原图与标准记录表宽度' });
  const sourceBefore = await sourcePane.boundingBox();
  const sourceDividerBox = await sourceDivider.boundingBox();
  expect(sourceBefore).not.toBeNull();
  expect(sourceDividerBox).not.toBeNull();
  await page.mouse.move(sourceDividerBox!.x + sourceDividerBox!.width / 2, sourceDividerBox!.y + 180);
  await page.mouse.down();
  await page.mouse.move(sourceDividerBox!.x + sourceDividerBox!.width / 2 + 80, sourceDividerBox!.y + 180);
  await page.mouse.up();
  const sourceAfter = await sourcePane.boundingBox();
  expect(sourceAfter!.width).toBeGreaterThan(sourceBefore!.width + 60);

  const inspector = page.getByRole('complementary', { name: '语义检查器' });
  const inspectorDivider = page.getByRole('separator', { name: '调整标准记录表与编辑面板宽度' });
  const inspectorBefore = await inspector.boundingBox();
  const inspectorDividerBox = await inspectorDivider.boundingBox();
  expect(inspectorBefore).not.toBeNull();
  expect(inspectorDividerBox).not.toBeNull();
  await page.mouse.move(inspectorDividerBox!.x + inspectorDividerBox!.width / 2, inspectorDividerBox!.y + 180);
  await page.mouse.down();
  await page.mouse.move(inspectorDividerBox!.x + inspectorDividerBox!.width / 2 - 60, inspectorDividerBox!.y + 180);
  await page.mouse.up();
  const inspectorAfter = await inspector.boundingBox();
  expect(inspectorAfter!.width).toBeGreaterThan(inspectorBefore!.width + 40);

  const savedLayout = await page.evaluate(() => localStorage.getItem('scoresheet-reader:pane-layout'));
  expect(savedLayout).toContain('source');
  expect(savedLayout).toContain('inspector');
  await page.reload();
  await expect.poll(async () => (await sourcePane.boundingBox())?.width ?? 0).toBeCloseTo(sourceAfter!.width, 0);
  await expect.poll(async () => (await inspector.boundingBox())?.width ?? 0).toBeCloseTo(inspectorAfter!.width, 0);
});

test('photo pan, wheel zoom, reload and overlay preserve SVG coordinates', async ({ page }) => {
  await page.goto('/');
  const onePixelPng = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
    'base64',
  );
  const [fileChooser] = await Promise.all([
    page.waitForEvent('filechooser'),
    page.getByRole('button', { name: '导入记录表照片' }).click(),
  ]);
  await fileChooser.setFiles({
    name: 'private-style-test.png',
    mimeType: 'image/png',
    buffer: onePixelPng,
  });
  await expect(page.getByText('private-style-test.png')).toBeVisible();
  await expect(page.getByRole('button', { name: '四角校正' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '顺时针旋转 90°' })).toHaveCount(0);

  const sourceCanvas = page.getByLabel('照片画布：拖动平移，滚轮缩放');
  const sourceZoom = page.getByRole('button', { name: '适合栏宽' });
  await sourceCanvas.hover();
  await page.mouse.wheel(0, -100);
  await expect(sourceZoom).toHaveText('110%');

  const sourceImage = page.getByRole('img', { name: '上传的篮球记录表' });
  const sourceBeforeReload = await sourceImage.getAttribute('src');
  await page.getByRole('button', { name: '重新载入照片' }).click();
  await expect.poll(() => sourceImage.getAttribute('src')).not.toBe(sourceBeforeReload);

  for (let index = 0; index < 6; index += 1) await page.mouse.wheel(0, -100);
  const scrollBeforePan = await sourceCanvas.evaluate((element) => element.scrollLeft);
  const canvasBox = await sourceCanvas.boundingBox();
  expect(canvasBox).not.toBeNull();
  await page.mouse.move(canvasBox!.x + canvasBox!.width / 2, canvasBox!.y + canvasBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(canvasBox!.x + canvasBox!.width / 2 - 70, canvasBox!.y + canvasBox!.height / 2);
  await page.mouse.up();
  await expect.poll(() => sourceCanvas.evaluate((element) => element.scrollLeft)).toBeGreaterThan(scrollBeforePan);
  await page.getByRole('button', { name: '撤回照片视图' }).click();
  await expect.poll(() => sourceCanvas.evaluate((element) => element.scrollLeft)).toBeLessThanOrEqual(scrollBeforePan + 1);

  const overlay = page.locator('svg.scene-overlay');
  await expect(overlay).toHaveAttribute('viewBox', '0 0 595.32 842.04');
  const stage = page.locator('.page-stage');
  const initialWidth = await stage.evaluate((element) => element.getBoundingClientRect().width);
  const documentCanvas = page.getByLabel('标准记录表画布：拖动平移，滚轮缩放');
  await documentCanvas.hover();
  await page.mouse.wheel(0, -100);
  await expect.poll(() => stage.evaluate((element) => element.getBoundingClientRect().width)).toBeGreaterThan(initialWidth);
  await expect(overlay).toHaveAttribute('viewBox', '0 0 595.32 842.04');
  const scrollBeforeDocumentPan = await documentCanvas.evaluate((element) => element.scrollTop);
  const documentCanvasBox = await documentCanvas.boundingBox();
  expect(documentCanvasBox).not.toBeNull();
  await page.mouse.move(documentCanvasBox!.x + documentCanvasBox!.width / 2, documentCanvasBox!.y + documentCanvasBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(documentCanvasBox!.x + documentCanvasBox!.width / 2, documentCanvasBox!.y + documentCanvasBox!.height / 2 - 70);
  await page.mouse.up();
  await expect.poll(() => documentCanvas.evaluate((element) => element.scrollTop)).toBeGreaterThan(scrollBeforeDocumentPan);

  await page.getByRole('button', { name: '切换原图叠加' }).click();
  await expect(page.getByLabel('原图透明度')).toBeVisible();
  await page.getByLabel('原图透明度').fill('0.35');
  await expect(overlay).toHaveAttribute('viewBox', '0 0 595.32 842.04');
});

test('persisted synthetic sheet confirms and exports a printable PDF', async ({ page }) => {
  await page.goto('/');
  const createResponse = page.waitForResponse((response) =>
    response.url().endsWith('/api/v1/fixtures/synthetic') &&
    response.request().method() === 'POST' &&
    response.ok(),
  );
  await page.getByRole('button', { name: /合成样表/ }).click();
  await createResponse;
  await expect(page.getByRole('link', { name: /导出 PDF/ })).toBeVisible();

  const validationResponse = page.waitForResponse((response) =>
    response.url().endsWith('/validate') &&
    response.request().method() === 'POST' &&
    response.ok(),
  );
  await page.getByRole('button', { name: /校验/ }).click();
  await validationResponse;
  await expect(page.getByText('当前没有发现确定性冲突')).toBeVisible();

  const confirmResponse = page.waitForResponse((response) =>
    response.url().endsWith('/confirm') &&
    response.request().method() === 'POST' &&
    response.ok(),
  );
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: /提交记录表/ }).click();
  await confirmResponse;
  await expect(page.locator('.document-state')).toContainText('已提交');

  const exportLink = page.getByRole('link', { name: /导出 PDF/ });
  const href = await exportLink.getAttribute('href');
  expect(href).toMatch(/render\.pdf$/);
  const pdfPrefix = await page.evaluate(async (url) => {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`PDF export failed: ${response.status}`);
    const bytes = new Uint8Array(await response.arrayBuffer());
    return String.fromCharCode(...bytes.subarray(0, 4));
  }, href!);
  expect(pdfPrefix).toBe('%PDF');

  await page.screenshot({ path: '../output/playwright/editor-workspace.png', fullPage: true });
});
