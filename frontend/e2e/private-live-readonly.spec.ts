import { expect, test } from '@playwright/test';

test.skip(
  process.env.RUN_PRIVATE_LIVE_UI !== '1',
  '私有记录表只在显式设置 RUN_PRIVATE_LIVE_UI=1 时做只读页面核对。',
);

test('current private reference sheet opens without mutation', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: /选择比赛/ }).click();
  const dialog = page.getByRole('dialog', { name: '选择比赛' });
  const game = dialog.getByRole('button', { name: /数学.*外院.*已提交/ });
  await expect(game).toBeVisible();
  await game.click();
  await expect(dialog).toHaveCount(0);

  const documentSnapshot = await page.evaluate(async () => {
    const games = await fetch('/api/v1/games').then((response) => response.json());
    const currentGame = games.find(
      (item: { team_a_name: string; team_b_name: string }) =>
        item.team_a_name === '数学' && item.team_b_name === '外院',
    );
    if (!currentGame?.document_id) throw new Error('数学 vs 外院记录表未关联到比赛。');
    return fetch(`/api/v1/documents/${currentGame.document_id}`).then((response) => response.json());
  });
  expect(documentSnapshot.score_events).toHaveLength(34);
  expect(documentSnapshot.stated_period_scores).toEqual([
    { period: 1, team_a: 9, team_b: 4 },
    { period: 2, team_a: 10, team_b: 7 },
    { period: 3, team_a: 12, team_b: 8 },
    { period: 4, team_a: 3, team_b: 6 },
  ]);
  expect(documentSnapshot.final_score).toMatchObject({
    team_a: 34,
    team_b: 25,
    winner_name: '数学',
  });

  const reviewList = page.getByLabel('待人工核对的识别字段');
  await expect(reviewList).toContainText('B 队第 11 个旧识别得分候选项');
  await expect(reviewList).toContainText('B 队第 4 节结束累计分');
  await expect(reviewList).toContainText('A 队助理教练员姓名');
  await expect(reviewList.getByRole('button', { name: /定位：/ })).toHaveCount(6);
  await expect(reviewList.getByRole('button', { name: /已核对：/ })).toHaveCount(6);

  await page.locator('rect[data-field-id="score.A.012"]').dblclick();
  await expect(page.getByRole('tab', { name: /Q2/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('[data-score-field="score.A.012"]')).toHaveClass(/is-targeted/);

  await expect(page.locator('line[data-field-id="team.A.coach_foul.unused"]')).toHaveCount(1);
  await expect(page.locator('line[data-field-id="team.A.assistant_coach_foul.unused"]')).toHaveCount(1);
  await expect(page.locator('line[data-field-id="team.B.coach_foul.unused"]')).toHaveCount(1);
  await expect(page.locator('line[data-field-id="team.B.assistant_coach_foul.unused"]')).toHaveCount(1);

  await page.locator('rect[data-field-id="summary.winner"]').dblclick();
  const winner = page.getByLabel('胜队');
  await expect(winner).toHaveValue('数学');
  await expect(winner.locator('option')).toHaveText(['未填写', '数学', '外院']);

  await page.screenshot({
    path: '../output/playwright/private-live-readonly.png',
    fullPage: true,
  });
});
