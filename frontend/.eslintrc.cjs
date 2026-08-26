module.exports = {
  root: true,
  env: { browser: true, es2020: true },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react-hooks/recommended',
  ],
  ignorePatterns: ['dist', '.eslintrc.cjs', 'node_modules'],
  parser: '@typescript-eslint/parser',
  plugins: ['react-refresh'],
  rules: {
    // 历史代码中大量使用显式 any（types/api 层），逐步迁移后再开启
    '@typescript-eslint/no-explicit-any': 'off',
    // useEffect 依赖数组缺失属既有行为，自动补依赖可能改变执行时序，暂不强制
    'react-hooks/exhaustive-deps': 'off',
    // 部分文件同时导出组件与常量/工具函数（Fast Refresh 限制），结构问题暂不强制
    'react-refresh/only-export-components': 'off',
    // 未使用变量报错；下划线前缀用于有意保留的占位
    '@typescript-eslint/no-unused-vars': [
      'error',
      { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
    ],
  },
}
