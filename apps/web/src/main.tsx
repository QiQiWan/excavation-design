import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './app/App';
import AppErrorBoundary from './app/AppErrorBoundary';
import './app/styles.css';

const application = <AppErrorBoundary><App /></AppErrorBoundary>;
const strictModeEnabled = String(import.meta.env.VITE_REACT_STRICT_MODE ?? '').toLowerCase() === 'true';

// WebGL viewers and resumable task effects mount once by default. StrictMode
// remains opt-in for focused diagnostics with VITE_REACT_STRICT_MODE=true.
ReactDOM.createRoot(document.getElementById('root')!).render(
  strictModeEnabled ? <React.StrictMode>{application}</React.StrictMode> : application,
);
