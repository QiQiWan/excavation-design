import { act, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import {
  beginGlobalActivity,
  finishGlobalActivity,
  GlobalRequestProgress,
  updateGlobalActivity,
} from './GlobalRequestProgress';

describe('GlobalRequestProgress page overlay', () => {
  it('covers the page and follows explicit workflow progress', async () => {
    render(<GlobalRequestProgress />);
    let id = '';
    act(() => { id = beginGlobalActivity({
      label: '正在运行完整计算',
      phase: '建立施工阶段模型',
      progress: 12,
      blocking: true,
    }); });

    expect(await screen.findByText('正在运行完整计算')).toBeInTheDocument();
    const overlay = screen.getByRole('presentation');
    expect(overlay).toHaveClass('globalRequestOverlay', 'blocking');
    expect(screen.getByText('12%')).toBeInTheDocument();

    act(() => updateGlobalActivity(id, { phase: '求解支撑轴力', progress: 58 }));
    await waitFor(() => expect(screen.getByText('求解支撑轴力')).toBeInTheDocument());
    expect(screen.getByText('58%')).toBeInTheDocument();

    act(() => finishGlobalActivity(id, { ok: true, phase: '完整计算已完成' }));
    await waitFor(() => expect(screen.getByText('完整计算已完成')).toBeInTheDocument());
    expect(overlay).toHaveClass('done');
  });

  it('delays and expires passive covers so polling reads do not stick', () => {
    vi.useFakeTimers();
    try {
      render(<GlobalRequestProgress />);
      act(() => { beginGlobalActivity({ label: '正在加载项目列表', blocking: false, progress: 30 }); });
      // 3 秒内不显示：轮询请求不应让遮罩一直闪。
      act(() => { vi.advanceTimersByTime(1000); });
      expect(screen.queryByText('正在加载项目列表')).not.toBeInTheDocument();
      act(() => { vi.advanceTimersByTime(2400); });
      expect(screen.getByText('正在加载项目列表')).toBeInTheDocument();
      expect(screen.getByRole('presentation')).toHaveClass('passive');
      // 20 秒仍未结束：自动隐藏，避免长期停在某个百分比。
      act(() => { vi.advanceTimersByTime(21000); });
      expect(screen.queryByText('正在加载项目列表')).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
