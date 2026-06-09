import React from 'react';
import { ApiOutlined, BranchesOutlined, ClockCircleOutlined, ExperimentOutlined, RocketOutlined } from '@ant-design/icons';

interface HomePageProps {
  onNavigateToAI: () => void;
  onNavigateToIoT: () => void;
  onNavigateToApiTest: () => void;
  onNavigateToScheduledTask: () => void;
  onNavigateToMock: () => void;
  canAccessScheduledTask?: boolean;
  canAccessMock?: boolean;
}

interface ToolEntry {
  key: string;
  title: string;
  label: string;
  summary: string;
  accent: string;
  tint: string;
  icon: React.ReactNode;
  onClick: () => void;
}

const HomePage: React.FC<HomePageProps> = ({
  onNavigateToAI,
  onNavigateToIoT,
  onNavigateToApiTest,
  onNavigateToScheduledTask,
  onNavigateToMock,
  canAccessScheduledTask = false,
  canAccessMock = false,
}) => {
  const tools: ToolEntry[] = [
    {
      key: 'ai-testcase',
      title: 'AI 测试用例平台',
      label: '用例生成',
      summary: '需求、图片、历史提示词集中处理，输出结构化测试用例。',
      accent: '#2563eb',
      tint: '#eff6ff',
      icon: <RocketOutlined />,
      onClick: onNavigateToAI,
    },
    {
      key: 'api-test',
      title: '接口场景测试工具',
      label: '接口编排',
      summary: 'OpenAPI 导入、请求调试、串行场景、变量提取和断言。',
      accent: '#0891b2',
      tint: '#ecfeff',
      icon: <BranchesOutlined />,
      onClick: onNavigateToApiTest,
    },
    {
      key: 'iot-push',
      title: 'IoT 数据推送平台',
      label: '请求调试',
      summary: '多方法请求、环境参数、文件参数和响应后置提取。',
      accent: '#16a34a',
      tint: '#f0fdf4',
      icon: <ApiOutlined />,
      onClick: onNavigateToIoT,
    },
  ];

  if (canAccessScheduledTask) {
    tools.push({
      key: 'scheduled-task',
      title: '定时任务平台',
      label: '任务调度',
      summary: '间隔、Cron、执行日志和多请求任务链路。',
      accent: '#ea580c',
      tint: '#fff7ed',
      icon: <ClockCircleOutlined />,
      onClick: onNavigateToScheduledTask,
    });
  }

  if (canAccessMock) {
    tools.push({
      key: 'mock-api',
      title: 'Mock 接口平台',
      label: '接口模拟',
      summary: 'URL 匹配、参数化响应、调用记录和分页模拟。',
      accent: '#7c3aed',
      tint: '#f5f3ff',
      icon: <ExperimentOutlined />,
      onClick: onNavigateToMock,
    });
  }

  const [primaryTool, ...secondaryTools] = tools;

  return (
    <main className="home-workbench">
      <section className="home-shell">
        <header className="home-header">
          <div>
            <p className="home-eyebrow">Test Tool Platform</p>
            <h1>测试工具平台</h1>
          </div>
          <div className="home-summary">
            <span>{tools.length} 个工具</span>
            <span>版本 1.0.0</span>
          </div>
        </header>

        <div className={`home-tool-grid ${secondaryTools.length < 3 ? 'home-tool-grid-compact' : ''}`}>
          {primaryTool && (
            <button
              type="button"
              className="home-tool-card home-tool-card-primary"
              style={{ '--tool-accent': primaryTool.accent, '--tool-tint': primaryTool.tint } as React.CSSProperties}
              onClick={primaryTool.onClick}
            >
              <span className="home-tool-tag">{primaryTool.label}</span>
              <span className="home-tool-icon">{primaryTool.icon}</span>
              <span className="home-tool-content">
                <strong>{primaryTool.title}</strong>
                <span>{primaryTool.summary}</span>
              </span>
            </button>
          )}

          <div className="home-secondary-grid">
            {secondaryTools.map((tool) => (
              <button
                key={tool.key}
                type="button"
                className="home-tool-card"
                style={{ '--tool-accent': tool.accent, '--tool-tint': tool.tint } as React.CSSProperties}
                onClick={tool.onClick}
              >
                <span className="home-tool-topline">
                  <span className="home-tool-icon">{tool.icon}</span>
                  <span className="home-tool-tag">{tool.label}</span>
                </span>
                <span className="home-tool-content">
                  <strong>{tool.title}</strong>
                  <span>{tool.summary}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      </section>
    </main>
  );
};

export default HomePage;
