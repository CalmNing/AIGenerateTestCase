/**
 * 全局悬浮入口（变量助手 / AI 助手 / Mock 流量代理）的层级常量。
 *
 * Ant Design 的 FloatButton 默认使用 zIndexPopupBase（1000），与 Modal 同层，
 * 因为在 DOM 末尾渲染而总是盖在弹窗、抽屉与各平台面板之上。
 * 这里统一降到普通内容层级之上、弹窗之下，既保证入口可见可点，
 * 又不会遮挡任何浮层。
 *
 * 注意：Popover / Drawer / Modal 等浮层的弹层会 portal 到 body 并使用自己的
 * z-index（1000+），因此降低触发按钮层级不会影响弹层自身的显示。
 */
export const FLOAT_BUTTON_Z_INDEX = 90;
