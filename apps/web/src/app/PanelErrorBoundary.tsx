import React from 'react';

type Props = React.PropsWithChildren<{ title: string; resetKey?: string }>;
type State = { error?: Error };

/**
 * Isolates heavy engineering panels from the application shell.
 * A chart/viewer failure must not discard the project workspace or force the
 * entire application into the root recovery screen.
 */
export default class PanelErrorBoundary extends React.Component<Props, State> {
  state: State = {};

  static getDerivedStateFromError(error: Error): State { return { error }; }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(`[PitGuard] ${this.props.title} panel failure`, error, info);
  }

  componentDidUpdate(previous: Props) {
    if (this.state.error && previous.resetKey !== this.props.resetKey) this.setState({ error: undefined });
  }

  private retry = () => this.setState({ error: undefined });

  render() {
    if (!this.state.error) return this.props.children;
    return <section className="panelRecoveryCard" role="alert">
      <div><strong>{this.props.title}暂时无法显示</strong><span>项目数据和后台任务未受影响。可重试该面板，其他设计步骤仍可继续使用。</span></div>
      <code>{this.state.error.message}</code>
      <button type="button" className="secondary" onClick={this.retry}>重试此面板</button>
    </section>;
  }
}
