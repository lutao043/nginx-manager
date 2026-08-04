/* ui.js — DOM 快捷工具与通用交互（弹窗/提示） */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

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
