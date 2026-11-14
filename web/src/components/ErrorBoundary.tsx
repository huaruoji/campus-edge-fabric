import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/** 兜底错误边界：任何渲染异常都不允许出现白屏。 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[CEF Dashboard] 渲染异常", error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="crash" role="alert">
          <h1>仪表盘渲染出错</h1>
          <p>页面遇到了未预期的错误，但后端与数据不受影响。刷新页面即可重试。</p>
          <pre>{this.state.error.message}</pre>
          <button type="button" className="btn btn--primary" onClick={() => window.location.reload()}>
            刷新页面
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
