// test_engine.js — 定投管理程序策略引擎回归测试
// 用法: node test_engine.js
// 原理: 从 定投管理程序.html / assets/dca-manager.html 提取 <script> 实际代码，
//       注入最小 DOM stub 后执行，对策略引擎做单元断言 + 输出一次完整模拟的指标摘要。
"use strict";
const fs = require("fs");
const vm = require("vm");
const path = require("path");

// 兼容两种布局：skill 打包布局(../assets/dca-manager.html) 与 原项目布局(同目录 定投管理程序.html)
const candidates = [
  path.join(__dirname, "..", "assets", "dca-manager.html"),
  path.join(__dirname, "定投管理程序.html"),
];
const htmlPath = candidates.find(p => fs.existsSync(p));
if (!htmlPath) { console.error("未找到定投管理程序 HTML（预期 assets/dca-manager.html 或同目录 定投管理程序.html）"); process.exit(1); }
const html = fs.readFileSync(htmlPath, "utf8");
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error("未找到 <script> 块"); process.exit(1); }

/* ---------- 最小 DOM / 浏览器环境 stub ---------- */
const makeEl = () => ({
  value: "", checked: false, textContent: "", innerHTML: "", style: {},
  classList: { add() {}, remove() {} },
  querySelectorAll: () => [], setAttribute() {}, appendChild() {},
  getBoundingClientRect: () => ({ width: 900, height: 260 }),
});
const els = {};
const sandbox = {
  console,
  Math, Date, JSON, Number, String, Object, Array, parseFloat, parseInt, isNaN,
  document: {
    getElementById: id => (els[id] || (els[id] = makeEl())),
    createElementNS: () => makeEl(),
    createElement: () => makeEl(),
  },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  Blob: class { constructor() {} },
  URL: { createObjectURL: () => "", revokeObjectURL() {} },
  // 动画定时器同步立即执行，避免测试等待真实 3.6s 动画
  setTimeout: fn => { fn(); return 0; },
  clearTimeout: () => {},
  TextDecoder,
};
sandbox.window = sandbox;
vm.createContext(sandbox);

const testCode = m[1] + `
;(function runTests(){
  const out = {};

  /* ---------- 用例 1：恒高于上限 → 全部暂停，0 买入 ---------- */
  {
    const cfg = Object.assign({}, DEFAULT_CONFIG, { startPrice: 10, upperPrice: 9, lowerPrice: 8, simDays: 100 });
    const prices = new Array(100).fill(9.5);
    const r = runStrategy(cfg, prices);
    if (r.buys !== 0) throw new Error("用例1失败: 应全部暂停, 实际买入 " + r.buys);
    if (r.skips !== 20) throw new Error("用例1失败: 每周频率100天应暂停20期, 实际 " + r.skips);
    out.case1 = "OK 高于上限全部暂停 (买入0/暂停20)";
  }

  /* ---------- 用例 2：恒低于下限 → 每次加码买入 ---------- */
  {
    const cfg = Object.assign({}, DEFAULT_CONFIG, { startPrice: 10, upperPrice: 12, lowerPrice: 8, multiply: 2, simDays: 100 });
    const prices = new Array(100).fill(7);
    const r = runStrategy(cfg, prices);
    if (r.buys !== 20) throw new Error("用例2失败: 应买入20期, 实际 " + r.buys);
    if (r.boosts !== 20) throw new Error("用例2失败: 应全部为加码, 实际加码 " + r.boosts);
    const first = r.trades[0];
    if (Math.abs(first.amount - 10000) > 1e-6) throw new Error("用例2失败: 加码金额应为 5000*2=10000, 实际 " + first.amount);
    out.case2 = "OK 低于下限全部加码买入 (20期 × ¥10000)";
  }

  /* ---------- 用例 3：固定止盈触发 ---------- */
  {
    const cfg = Object.assign({}, DEFAULT_CONFIG, { takeProfitPct: 20, stopLossPct: 10, trailEnabled: false,
      sellRatio: 1, frequency: "weekly", simDays: 60 });
    // 前 25 天 10 元(每周买入), 后 35 天 13 元(浮盈+30% → 触发止盈)
    const prices = [];
    for (let i = 0; i < 25; i++) prices.push(10);
    for (let i = 25; i < 60; i++) prices.push(13);
    const r = runStrategy(cfg, prices);
    if (r.tps < 1) throw new Error("用例3失败: 应触发止盈, 实际 tps=" + r.tps);
    if (r.shares !== 0) throw new Error("用例3失败: 全部止盈后应无持仓, 实际 shares=" + r.shares);
    out.case3 = "OK 固定止盈触发 (" + r.tps + " 次, 清仓后现金 " + r.cash.toFixed(2) + ")";
  }

  /* ---------- 用例 4：止损触发 ---------- */
  {
    const cfg = Object.assign({}, DEFAULT_CONFIG, { takeProfitPct: 50, stopLossPct: 10, trailEnabled: false,
      sellRatio: 1, frequency: "weekly", simDays: 60 });
    const prices = [];
    for (let i = 0; i < 25; i++) prices.push(10);
    for (let i = 25; i < 60; i++) prices.push(8.8); // 浮盈 -12% → 止损
    const r = runStrategy(cfg, prices);
    if (r.sls < 1) throw new Error("用例4失败: 应触发止损, 实际 sls=" + r.sls);
    out.case4 = "OK 止损触发 (" + r.sls + " 次, 清仓)";
  }

  /* ---------- 用例 5：复利再投入 —— 止盈资金滚入池子继续定投 ---------- */
  {
    const cfg = Object.assign({}, DEFAULT_CONFIG, { takeProfitPct: 20, trailEnabled: false, reinvest: true,
      frequency: "daily", simDays: 200, principal: 100000, periodAmount: 5000 });
    const prices = [];
    for (let i = 0; i < 50; i++) prices.push(10);     // 每天 10 元买入
    for (let i = 50; i < 100; i++) prices.push(13);   // +30% → 止盈清仓
    for (let i = 100; i < 150; i++) prices.push(10);  // 回落继续买入
    for (let i = 150; i < 200; i++) prices.push(12);  // +20% → 再次止盈
    const r = runStrategy(cfg, prices);
    if (r.tps < 2) throw new Error("用例5失败: 两轮止盈应至少2次, 实际 " + r.tps);
    // 复利应使累计已实现收益 > 0 且第二轮仍有钱买入
    if (r.realizedProfit <= 0) throw new Error("用例5失败: 已实现收益应 > 0");
    if (r.buys < 60) throw new Error("用例5失败: 第二轮应有买入, 实际买入 " + r.buys);
    // 复利核心验证：累计买入总金额 > 本金（止盈资金滚入资金池继续定投）
    const totalInvested = r.trades.filter(t => t.type === "BUY").reduce((s, t) => s + t.amount, 0);
    if (totalInvested <= cfg.principal) throw new Error("用例5失败: 累计投入应 > 本金(复利生效), 投入 " + totalInvested.toFixed(0) + " ≤ " + cfg.principal);
    out.case5 = "OK 复利再投入 (止盈 " + r.tps + " 次, 已实现收益 " + r.realizedProfit.toFixed(0)
      + ", 累计投入 " + totalInvested.toFixed(0) + " > 本金 " + cfg.principal + ")";
  }

  /* ---------- 用例 6：移动止盈 回撤触发 ---------- */
  {
    const cfg = Object.assign({}, DEFAULT_CONFIG, { trailEnabled: true, trailTriggerPct: 10, trailDrawdownPct: 5,
      takeProfitPct: 999, stopLossPct: 999, frequency: "daily", simDays: 120, periodAmount: 1000 });
    const prices = [];
    for (let i = 0; i < 30; i++) prices.push(10);   // 买入, 成本10
    for (let i = 30; i < 60; i++) prices.push(12);  // +20% 激活移动止盈
    for (let i = 60; i < 90; i++) prices.push(12.5);// 新高
    for (let i = 90; i < 120; i++) prices.push(11.5);// 自高点回撤 8% ≥ 5% → 触发
    const r = runStrategy(cfg, prices);
    if (r.tps < 1) throw new Error("用例6失败: 移动止盈应触发, 实际 tps=" + r.tps);
    out.case6 = "OK 移动止盈回撤触发 (" + r.tps + " 次)";
  }

  /* ---------- 用例 7：GBM 模拟可复现 + 数值合理 ---------- */
  {
    const a = gbmSim(10, 0.08, 0.22, 504, 42);
    const b = gbmSim(10, 0.08, 0.22, 504, 42);
    if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error("用例7失败: 同种子结果不一致");
    if (a.length !== 504) throw new Error("用例7失败: 长度错误");
    if (a.some(v => !(v > 0))) throw new Error("用例7失败: 出现非正价格");
    out.case7 = "OK GBM 模拟可复现 (504天, 终值 " + a[503].toFixed(2) + ")";
  }

  out.summary = {
    simDays: RESULT.cfg.simDays,
    buys: RESULT.buys, boosts: RESULT.boosts, skips: RESULT.skips,
    tps: RESULT.tps, sls: RESULT.sls,
    finalEquity: RESULT.finalEquity,
    totalReturn: (RESULT.totalReturn * 100).toFixed(2) + "%",
    annualReturn: (RESULT.annualReturn * 100).toFixed(2) + "%",
    maxDrawdown: (RESULT.mdd * 100).toFixed(2) + "%",
    sharpe: RESULT.sharpe.toFixed(2),
    winRate: RESULT.closed ? (RESULT.winRate * 100).toFixed(1) + "%" : "--",
    trades: RESULT.trades.length,
  };

  console.log(JSON.stringify(out, null, 2));
  console.log("\\n✅ 全部用例通过");
})(this);`;

try {
  vm.runInContext(testCode, sandbox, { filename: "定投管理程序.html" });
} catch (e) {
  console.error("❌ 测试失败:", e.message);
  console.error(e.stack);
  process.exit(1);
}
