import React from 'react';
import { Empty } from 'antd';

/**
 * 轻量 Markdown 渲染：标题(#~######)、表格(|)、无序/有序列表、
 * 分隔线、**粗体**、`行内代码`。纯 React 元素输出，不注入 HTML。
 * 需求正文来自飞书 blocks 重建的 Markdown（标题级别权威），按原层级渲染。
 */

const HEADING_STYLE: Record<number, React.CSSProperties> = {
  1: { fontSize: 20, fontWeight: 600, margin: '18px 0 10px', paddingBottom: 6, borderBottom: '1px solid #f0f0f0' },
  2: { fontSize: 17, fontWeight: 600, margin: '16px 0 8px' },
  3: { fontSize: 15, fontWeight: 600, margin: '14px 0 6px' },
  4: { fontSize: 14, fontWeight: 600, margin: '12px 0 6px' },
  5: { fontSize: 13, fontWeight: 600, margin: '10px 0 4px', color: '#555' },
  6: { fontSize: 13, fontWeight: 600, margin: '10px 0 4px', color: '#888' },
};

const renderInline = (text: string, keyPrefix: string): React.ReactNode[] => {
  const nodes: React.ReactNode[] = [];
  const re = /\*\*([^*]+)\*\*|`([^`]+)`/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[1] !== undefined) {
      nodes.push(<strong key={keyPrefix + 'b' + i}>{m[1]}</strong>);
    } else if (m[2] !== undefined) {
      nodes.push(
        <code key={keyPrefix + 'c' + i} style={{ background: '#f5f5f5', padding: '1px 5px', borderRadius: 3, fontSize: '0.9em' }}>
          {m[2]}
        </code>,
      );
    }
    last = m.index + m[0].length;
    i += 1;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
};

const isTableRow = (line: string): boolean => /^\s*\|.*\|/.test(line);
const isSeparatorRow = (line: string): boolean => /^\s*\|?[\s:|-]+\|?\s*$/.test(line) && line.includes('-');
const splitRow = (line: string): string[] =>
  line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());

const renderTable = (rows: string[], key: string): React.ReactNode => {
  const dataRows = rows.filter((r) => !isSeparatorRow(r)).map(splitRow);
  if (dataRows.length === 0) return null;
  const [header, ...body] = dataRows;
  const cellStyle: React.CSSProperties = {
    border: '1px solid #e8e8e8',
    padding: '6px 10px',
    fontSize: 13,
    textAlign: 'left',
    wordBreak: 'break-word',
  };
  return (
    <table key={key} style={{ borderCollapse: 'collapse', margin: '10px 0', width: '100%' }}>
      <thead>
        <tr style={{ background: '#fafafa' }}>
          {header.map((c, i) => (
            <th key={i} style={{ ...cellStyle, fontWeight: 600 }}>{renderInline(c, key + 'h' + i)}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {body.map((row, ri) => (
          <tr key={ri}>
            {row.map((c, ci) => (
              <td key={ci} style={cellStyle}>{renderInline(c, key + 't' + ri + '-' + ci)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
};

const MarkdownView: React.FC<{ content: string }> = ({ content }) => {
  const blocks = React.useMemo(() => {
    const lines = (content || '').replace(/\r\n/g, '\n').split('\n');
    const out: React.ReactNode[] = [];
    let para: string[] = [];
    let table: string[] = [];
    let listItems: { ordered: boolean; text: string }[] = [];
    let key = 0;

    const flushPara = () => {
      if (para.length > 0) {
        out.push(
          <p key={'p' + key++} style={{ margin: '6px 0', lineHeight: 1.8, fontSize: 14, whiteSpace: 'pre-wrap' }}>
            {para.map((line, i) => (
              <React.Fragment key={i}>
                {i > 0 && <br />}
                {renderInline(line, 'p' + key + '-' + i)}
              </React.Fragment>
            ))}
          </p>,
        );
        para = [];
      }
    };
    const flushTable = () => {
      if (table.length > 0) {
        out.push(renderTable(table, 'tb' + key++));
        table = [];
      }
    };
    const flushList = () => {
      if (listItems.length > 0) {
        const ordered = listItems[0].ordered;
        const style: React.CSSProperties = { margin: '6px 0 6px 22px', padding: 0, lineHeight: 1.8, fontSize: 14 };
        out.push(
          ordered ? (
            <ol key={'ol' + key++} style={style}>
              {listItems.map((li, i) => <li key={i}>{renderInline(li.text, 'li' + key + '-' + i)}</li>)}
            </ol>
          ) : (
            <ul key={'ul' + key++} style={style}>
              {listItems.map((li, i) => <li key={i}>{renderInline(li.text, 'li' + key + '-' + i)}</li>)}
            </ul>
          ),
        );
        listItems = [];
      }
    };
    const flushAll = () => { flushPara(); flushTable(); flushList(); };

    for (const raw of lines) {
      const line = raw.replace(/\s+$/, '');
      const trimmed = line.trim();

      if (isTableRow(trimmed)) { flushPara(); flushList(); table.push(trimmed); continue; }
      flushTable();

      if (trimmed === '') { flushPara(); flushList(); continue; }

      const hm = trimmed.match(/^(#{1,6})\s+(.*)$/);
      if (hm) {
        flushAll();
        const level = hm[1].length;
        out.push(
          React.createElement('h' + level, { key: 'h' + key++, style: HEADING_STYLE[level] }, renderInline(hm[2], 'hh' + key)),
        );
        continue;
      }

      if (/^(---+|\*\*\*+)$/.test(trimmed)) {
        flushAll();
        out.push(<hr key={'hr' + key++} style={{ border: 'none', borderTop: '1px solid #e8e8e8', margin: '14px 0' }} />);
        continue;
      }

      const ul = trimmed.match(/^[-*]\s+(.*)$/);
      if (ul) {
        flushPara();
        flushTable();
        if (listItems.length > 0 && listItems[0].ordered) flushList();
        listItems.push({ ordered: false, text: ul[1] });
        continue;
      }
      const ol = trimmed.match(/^(\d{1,3})[.)、]\s*(.*)$/);
      if (ol) {
        flushPara();
        flushTable();
        if (listItems.length > 0 && !listItems[0].ordered) flushList();
        listItems.push({ ordered: true, text: ol[2] });
        continue;
      }

      flushTable();
      flushList();
      para.push(trimmed);
    }
    flushAll();
    return out;
  }, [content]);

  if (!content) return <Empty description="暂无需求内容" />;
  return <div style={{ color: 'rgba(0,0,0,0.88)' }}>{blocks}</div>;
};

export default MarkdownView;
