import React from 'react';

type State = { error?: Error };

export default class AppErrorBoundary extends React.Component<React.PropsWithChildren, State> {
  state: State = {};

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[PitGuard] frontend render failure', error, info);
  }

  private recover = () => {
    try {
      window.sessionStorage.removeItem('pitguard-active-task');
      window.localStorage.removeItem('pitguard-last-project');
    } catch {
      // Storage may be unavailable in privacy mode.
    }
    window.location.assign('/');
  };

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="fatalRecoveryPage" role="alert">
        <section className="card fatalRecoveryCard">
          <div className="loginBrandMark">PG</div>
          <h1>前端已进入安全恢复模式</h1>
          <p>计算进程或浏览器渲染出现异常，但项目数据仍保存在服务器。重新进入不会重复提交计算。</p>
          <pre>{this.state.error.message}</pre>
          <div className="buttonRow">
            <button onClick={() => window.location.reload()}>重新加载页面</button>
            <button className="secondary" onClick={this.recover}>清理临时界面状态并返回</button>
          </div>
        </section>
      </main>
    );
  }
}
