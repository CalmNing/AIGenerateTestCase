import React, { useEffect, useState } from 'react';
import { Button, Spin, Empty, message, Input } from 'antd';
import {
  ApiOutlined,
  BranchesOutlined,
  ClockCircleOutlined,
  ExperimentOutlined,
  RocketOutlined,
  AppstoreOutlined,
  SettingOutlined,
  LinkOutlined,
  ArrowRightOutlined,
  DownOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import { navLinkApi } from '../services/api';
import { NavLink, NavTreeNode } from '../types';
import NavLinkManagerModal from './NavLinkManagerModal';

interface HomePageProps {
  onNavigateToAI: () => void;
  onNavigateToIoT: () => void;
  onNavigateToApiTest: () => void;
  onNavigateToScheduledTask: () => void;
  onNavigateToMock: () => void;
  canAccessScheduledTask?: boolean;
  canAccessMock?: boolean;
  canManageNav?: boolean;
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
  canManageNav = false,
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

  // ===== 资源导航树 =====
  const [navTree, setNavTree] = useState<NavTreeNode[]>([]);
  const [navLoading, setNavLoading] = useState(false);
  const [managerVisible, setManagerVisible] = useState(false);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [navKeyword, setNavKeyword] = useState('');

  const loadNavTree = async () => {
    setNavLoading(true);
    try {
      const res = await navLinkApi.getTree();
      if (res.code === 200 && res.data) {
        setNavTree(res.data);
      } else {
        setNavTree([]);
      }
    } catch (e) {
      console.error('加载资源导航失败', e);
      setNavTree([]);
    } finally {
      setNavLoading(false);
    }
  };

  useEffect(() => {
    loadNavTree();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const totalLinks = navTree.reduce((sum, node) => sum + node.links.length, 0);

  const handleLinkClick = (link: NavLink) => {
    const safeUrl = link.url.trim();
    if (!safeUrl) {
      message.warning('链接地址为空');
      return;
    }
    if (link.open_type === 'self') {
      window.location.href = safeUrl;
    } else {
      window.open(safeUrl, '_blank', 'noopener,noreferrer');
    }
  };

  const toggleCollapse = (id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const keyword = navKeyword.trim().toLowerCase();
  const filteredTree = keyword
    ? navTree
        .map((node) => ({
          ...node,
          links: node.links.filter(
            (link) =>
              link.title.toLowerCase().includes(keyword) ||
              link.url.toLowerCase().includes(keyword) ||
              (link.description || '').toLowerCase().includes(keyword)
          ),
        }))
        .filter((node) => node.links.length > 0)
    : navTree;

  return (
    <main className="home-workbench">
      <section className="home-shell home-shell--top">
        {/* Hero */}
        <header className="home-header home-hero">
          <div>
            <p className="home-eyebrow">Test Tool Platform</p>
            <h1>测试工具平台</h1>
            <p className="home-hero-sub">一站式测试工作台：用例生成、接口编排、数据推送与常用资源导航。</p>
          </div>
          <div className="home-summary">
            <span>{tools.length} 个工具</span>
            <span>资源导航 {totalLinks} 条</span>
            <span>版本 1.0.0</span>
          </div>
        </header>

        {/* 工具箱 */}
        <section className="home-section">
          <div className="home-section-head">
            <h2>
              <AppstoreOutlined /> 工具箱
            </h2>
            <span className="home-section-hint">点击进入对应工具</span>
          </div>

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

        {/* 资源导航 */}
        <section className="home-section home-nav-section">
          <div className="home-section-head">
            <h2>
              <LinkOutlined /> 资源导航
            </h2>
            <div className="home-section-actions">
              <Input
                allowClear
                prefix={<SearchOutlined />}
                placeholder="搜索链接"
                value={navKeyword}
                onChange={(e) => setNavKeyword(e.target.value)}
                className="home-nav-search"
              />
              {canManageNav && (
                <Button size="small" icon={<SettingOutlined />} onClick={() => setManagerVisible(true)}>
                  管理
                </Button>
              )}
            </div>
          </div>

          <Spin spinning={navLoading}>
            {filteredTree.length === 0 ? (
              <div className="home-nav-empty">
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={
                    navTree.length === 0
                      ? canManageNav
                        ? '暂无导航条目，点击右上角「管理」添加'
                        : '暂无导航条目'
                      : '未找到匹配的链接'
                  }
                />
              </div>
            ) : (
              <div className="home-nav-groups">
                {filteredTree.map((node) => {
                  const expanded = keyword ? true : expandedIds.has(node.category.id);
                  return (
                    <div
                      className={`home-nav-group${expanded ? '' : ' home-nav-group-collapsed'}`}
                      key={node.category.id}
                    >
                      <button
                        type="button"
                        className="home-nav-group-head"
                        onClick={() => toggleCollapse(node.category.id)}
                        aria-expanded={expanded}
                      >
                        <span className="home-nav-group-icon">{node.category.icon || '📁'}</span>
                        <span className="home-nav-group-name">{node.category.name}</span>
                        <span className="home-nav-group-count">{node.links.length}</span>
                        <DownOutlined className={`home-nav-group-arrow${expanded ? '' : ' is-collapsed'}`} />
                      </button>
                      {expanded && (
                        <div className="home-nav-links">
                          {node.links.map((link) => (
                            <button
                              type="button"
                              className="home-nav-link"
                              key={link.id}
                              onClick={() => handleLinkClick(link)}
                              title={link.description || link.url}
                            >
                              <span className="home-nav-link-icon">{link.icon || '🔗'}</span>
                              <span className="home-nav-link-body">
                                <span className="home-nav-link-title">{link.title}</span>
                                <span className="home-nav-link-url">{link.url}</span>
                              </span>
                              <ArrowRightOutlined className="home-nav-link-arrow" />
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </Spin>
        </section>
      </section>

      {canManageNav && (
        <NavLinkManagerModal
          visible={managerVisible}
          onCancel={() => setManagerVisible(false)}
          onSaved={loadNavTree}
        />
      )}
    </main>
  );
};

export default HomePage;
