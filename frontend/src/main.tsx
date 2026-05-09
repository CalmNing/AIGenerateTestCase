import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import AppWithAuth from './AppWithAuth';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppWithAuth>
      <App />
    </AppWithAuth>
  </React.StrictMode>
);
