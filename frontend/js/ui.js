/* ui.js — DOM 快捷工具与通用交互（弹窗/提示） */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

/* 防抖：连续调用只执行最后一次（搜索输入等高频事件） */
function debounce(fn, ms) {
  let t = null;
  return function (...args) {
    clearTimeout(t);
    t = setTimeout(() => fn.apply(this, args), ms);
  };
}

/* 提示条 */
let toastTimer = null;
function toast(msg, type) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast" + (type ? " " + type : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
}

/* 确认弹窗：返回 Promise<boolean>（Esc/取消/点遮罩均视为否） */
function confirmDialog(text) {
  return new Promise((resolve) => {
    const mask = $("#confirmModal");
    const yesBtn = $("#btnConfirmYes");
    const noBtn = $("#btnConfirmNo");
    $("#confirmText").textContent = text;
    mask.hidden = false;
    yesBtn.focus(); // 回车直接确认

    const done = (val) => {
      mask.hidden = true;
      yesBtn.removeEventListener("click", onYes);
      noBtn.removeEventListener("click", onNo);
      mask.removeEventListener("click", onMask);
      document.removeEventListener("keydown", onKey);
      resolve(val);
    };
    const onYes = () => done(true);
    const onNo = () => done(false);
    const onMask = (e) => { if (e.target === mask) done(false); };
    const onKey = (e) => { if (e.key === "Escape") done(false); };

    yesBtn.addEventListener("click", onYes);
    noBtn.addEventListener("click", onNo);
    mask.addEventListener("click", onMask);
    document.addEventListener("keydown", onKey);
  });
}

/* 多选项确认弹窗：options = [{label, value, primary?}]，返回 Promise<value|null>
   点遮罩/取消/Esc 返回 null。用同一 confirmModal，动态重建按钮。 */
function confirmChoice(text, options) {
  return new Promise((resolve) => {
    const mask = $("#confirmModal");
    const foot = mask.querySelector(".modal-foot");
    $("#confirmText").textContent = text;
    // 清空旧的 yes/no 按钮，重建
    foot.innerHTML = "";
    const cancelBtn = document.createElement("button");
    cancelBtn.className = "btn";
    cancelBtn.type = "button";
    cancelBtn.textContent = "取消";
    foot.appendChild(cancelBtn);

    const done = (val) => {
      mask.hidden = true;
      foot.innerHTML = "";
      // 还原默认按钮
      const yes = document.createElement("button");
      yes.className = "btn btn-primary";
      yes.id = "btnConfirmYes";
      yes.type = "button";
      yes.textContent = "确认";
      const no = document.createElement("button");
      no.className = "btn";
      no.id = "btnConfirmNo";
      no.type = "button";
      no.textContent = "取消";
      foot.appendChild(no);
      foot.appendChild(yes);
      mask.removeEventListener("click", onMask);
      document.removeEventListener("keydown", onKey);
      resolve(val);
    };
    const onMask = (e) => { if (e.target === mask) done(null); };
    const onKey = (e) => { if (e.key === "Escape") done(null); };
    cancelBtn.addEventListener("click", () => done(null));
    options.forEach((opt) => {
      const btn = document.createElement("button");
      btn.className = "btn" + (opt.primary ? " btn-primary" : "");
      btn.type = "button";
      btn.textContent = opt.label;
      btn.addEventListener("click", () => done(opt.value));
      foot.appendChild(btn);
    });
    mask.addEventListener("click", onMask);
    document.addEventListener("keydown", onKey);
    mask.hidden = false;  // 显示弹窗
    const primary = foot.querySelector(".btn-primary");
    if (primary) primary.focus(); // 回车直接选主选项
  });
}

/* 打开/关闭弹窗：维护打开栈，Esc 关闭最上层 */
let modalStack = [];

function openModal(id) {
  const mask = $(id);
  if (!mask) return;
  if (!modalStack.includes(id)) modalStack.push(id);
  mask.hidden = false;
}

function closeModal(id) {
  const mask = $(id);
  if (mask) mask.hidden = true;
  modalStack = modalStack.filter((x) => x !== id);
}

/* Esc 关闭最上层弹窗（确认弹窗有自己的 Esc 处理，此处跳过） */
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !modalStack.length) return;
  const confirmMask = $("#confirmModal");
  if (confirmMask && !confirmMask.hidden) return;
  const top = $(modalStack[modalStack.length - 1]);
  if (!top) return;
  const closer = top.querySelector("[data-close]");
  if (closer) closer.click(); // 走统一关闭流程（含 onClose 回调）
  else { top.hidden = true; modalStack.pop(); }
});

/* 按键锁定（弹窗打开时锁 body 滚动，计数支持嵌套弹窗） */
let bodyLockCount = 0;
function lockBody() {
  bodyLockCount++;
  document.body.style.overflow = "hidden";
}
function unlockBody() {
  bodyLockCount = Math.max(0, bodyLockCount - 1);
  if (!bodyLockCount) document.body.style.overflow = "";
}

function bindModalClose(id, onClose) {
  const mask = $(id);
  if (!mask) return;
  const close = () => { closeModal(id); unlockBody(); if (onClose) onClose(); };
  mask.querySelectorAll("[data-close]").forEach((el) => el.addEventListener("click", close));
  mask.addEventListener("click", (e) => { if (e.target === mask) close(); });
}

/* ── 主题切换（4 配色 × 日夜，选择持久化到 localStorage(nm-theme)） ──
   用弹层色板替代原生 select：主题是「看着选」的，色块比文字更快辨认。 */
(function initThemeSwitcher() {
  const btn = document.getElementById("btnTheme");
  const pop = document.getElementById("themePopover");
  if (!btn || !pop) return;

  const GROUPS = [
    { label: "夜间模式", items: [
      { id: "emerald-dark",  name: "翡翠 · 夜", color: "#3ddc97" },
      { id: "ocean-dark",    name: "海洋 · 夜", color: "#4d9ef5" },
      { id: "amber-dark",    name: "琥珀 · 夜", color: "#f0a53c" },
      { id: "rose-dark",     name: "玫瑰 · 夜", color: "#f4728c" },
    ] },
    { label: "日间模式", items: [
      { id: "emerald-light", name: "翡翠 · 日", color: "#0a7f58" },
      { id: "ocean-light",   name: "海洋 · 日", color: "#1f6feb" },
      { id: "amber-light",   name: "琥珀 · 日", color: "#a9660a" },
      { id: "rose-light",    name: "玫瑰 · 日", color: "#c21640" },
    ] },
  ];

  GROUPS.forEach((g) => {
    const group = document.createElement("div");
    group.className = "theme-group";
    const label = document.createElement("span");
    label.className = "theme-group-label";
    label.textContent = g.label;
    group.appendChild(label);
    g.items.forEach((t) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "theme-item";
      item.dataset.theme = t.id;
      const swatch = document.createElement("span");
      swatch.className = "theme-swatch";
      swatch.style.background = t.color;
      const name = document.createElement("span");
      name.textContent = t.name;
      item.appendChild(swatch);
      item.appendChild(name);
      item.addEventListener("click", () => { applyTheme(t.id); closePicker(); });
      group.appendChild(item);
    });
    pop.appendChild(group);
  });

  const current = () => document.body.getAttribute("data-theme") || "emerald-dark";
  function sync() {
    Array.from(pop.querySelectorAll(".theme-item")).forEach((el) => {
      el.classList.toggle("active", el.dataset.theme === current());
    });
  }
  function applyTheme(id) {
    document.body.setAttribute("data-theme", id);
    localStorage.setItem("nm-theme", id);
    sync();
  }
  function openPicker() { pop.hidden = false; btn.setAttribute("aria-expanded", "true"); sync(); }
  function closePicker() { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); }

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (pop.hidden) openPicker(); else closePicker();
  });
  document.addEventListener("click", (e) => {
    if (!pop.hidden && !pop.contains(e.target)) closePicker();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !pop.hidden) closePicker();
  });
  applyTheme(current()); // 首次同步选中态（含从 localStorage 恢复的主题）
})();
