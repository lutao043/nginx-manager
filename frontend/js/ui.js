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

/* 确认弹窗：返回 Promise<boolean> */
function confirmDialog(text) {
  return new Promise((resolve) => {
    const mask = $("#confirmModal");
    const yesBtn = $("#btnConfirmYes");
    const noBtn = $("#btnConfirmNo");
    $("#confirmText").textContent = text;
    mask.hidden = false;

    const done = (val) => {
      mask.hidden = true;
      yesBtn.removeEventListener("click", onYes);
      noBtn.removeEventListener("click", onNo);
      mask.removeEventListener("click", onMask);
      resolve(val);
    };
    const onYes = () => done(true);
    const onNo = () => done(false);
    const onMask = (e) => { if (e.target === mask) done(false); };

    yesBtn.addEventListener("click", onYes);
    noBtn.addEventListener("click", onNo);
    mask.addEventListener("click", onMask);
  });
}

/* 多选项确认弹窗：options = [{label, value, primary?}]，返回 Promise<value|null>
   点遮罩/取消返回 null。用同一 confirmModal，动态重建按钮。 */
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
      resolve(val);
    };
    const onMask = (e) => { if (e.target === mask) done(null); };
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
    mask.hidden = false;  // 显示弹窗
  });
}

/* 打开设置弹窗 */
function openModal(id) {
  const mask = $(id);
  if (mask) mask.hidden = false;
}

function closeModal(id) {
  const mask = $(id);
  if (mask) mask.hidden = true;
}

/* 按键锁定（弹窗打开时锁 body 滚动） */
let bodyLocked = false;
function lockBody() {
  if (bodyLocked) return;
  bodyLocked = true;
  document.body.style.overflow = "hidden";
}
function unlockBody() {
  bodyLocked = false;
  document.body.style.overflow = "";
}

function bindModalClose(id, onClose) {
  const mask = $(id);
  if (!mask) return;
  const close = () => { closeModal(id); unlockBody(); if (onClose) onClose(); };
  mask.querySelectorAll("[data-close]").forEach((el) => el.addEventListener("click", close));
  mask.addEventListener("click", (e) => { if (e.target === mask) close(); });
}

/* ── 主题切换 ── */
(function initThemeSwitcher() {
  const sel = document.getElementById("themeSelect");
  if (!sel) return;
  const current = document.body.getAttribute("data-theme") || "emerald-dark";
  sel.value = current;
  sel.addEventListener("change", () => {
    document.body.setAttribute("data-theme", sel.value);
    localStorage.setItem("nm-theme", sel.value);
  });
})();
