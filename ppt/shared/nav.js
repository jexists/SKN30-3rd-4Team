/* ==========================================================================
   홈쉴드(HomeShield) 발표 슬라이드 — 공통 내비게이션 스크립트
   file:// 로컬 실행 호환 (fetch/모듈 import 없이 순수 <script> 태그로 로드)

   기능:
   - SLIDES 배열 = 슬라이드 순서·제목의 단일 소스 (추가/삭제 시 여기만 수정)
   - 키보드: ←/PageUp 이전, →/Space/PageDown 다음, Home/End 처음/끝,
             T/Esc 목차 토글, F 전체화면 토글. ↑↓·휠은 슬라이드 내부 스크롤에 사용.
   - 우하단 플로팅 이전/다음 버튼 + 페이지 표시(클릭 시 목차) + 전체화면 버튼
   - 최하단 5px 프로그레스 바 (현재 위치/전체 비율)
   - .topbar-page 요소가 있으면 "n / N" 자동 기입
   - 16:9(1280x720) 캔버스 스케일 자동 조정
   ========================================================================== */

/* 슬라이드 순서·제목의 단일 소스 (Single Source of Truth) */
var SLIDES = [
  { file: "01_cover.html", title: "표지" },
  { file: "02_toc.html", title: "목차" },
  { file: "03_team.html", title: "팀 소개" },
  { file: "04_problem.html", title: "문제 정의 · 선정 배경" },
  { file: "05_goal.html", title: "목표 & 차별점" },
  { file: "06_stack.html", title: "기술 스택" },
  { file: "07_data.html", title: "사용한 데이터" },
  { file: "08_pipeline.html", title: "데이터 파이프라인" },
  { file: "09_erd_v1.html", title: "ERD ① 물리 + 논리 스키마" },
  { file: "10_erd_v2.html", title: "ERD ② 단일 테이블" },
  { file: "11_langgraph.html", title: "LangGraph 전체 구조" },
  { file: "12_langgraph_detail.html", title: "pre 서브그래프 & 품질 루프" },
  { file: "13_features.html", title: "핵심 기능" },
  { file: "14_eval.html", title: "성능 평가" },
  { file: "15_demo.html", title: "프로젝트 시연" },
  { file: "16_issues.html", title: "주요 이슈 및 해결" },
  { file: "17_future.html", title: "확장 가능성 & 향후 과제" },
  { file: "18_closing.html", title: "마무리" }
];

(function () {
  "use strict";

  function getCurrentFile() {
    var path = window.location.pathname.replace(/\\/g, "/");
    var parts = path.split("/");
    return parts[parts.length - 1] || SLIDES[0].file;
  }

  function getCurrentIndex() {
    var current = getCurrentFile();
    for (var i = 0; i < SLIDES.length; i++) {
      if (SLIDES[i].file === current) return i;
    }
    return 0;
  }

  function goTo(index) {
    if (index < 0 || index >= SLIDES.length) return;
    window.location.href = SLIDES[index].file;
  }

  function goNext() { var i = getCurrentIndex(); if (i < SLIDES.length - 1) goTo(i + 1); }
  function goPrev() { var i = getCurrentIndex(); if (i > 0) goTo(i - 1); }
  function goFirst() { goTo(0); }
  function goLast() { goTo(SLIDES.length - 1); }

  /* ------------------------------------------------------------------
     16:9 스케일 프레임 리사이즈 (.slide-canvas 없는 슬라이드는 스킵)
     ------------------------------------------------------------------ */
  function fitCanvas() {
    var canvas = document.querySelector(".slide-canvas");
    var viewport = document.querySelector(".slide-viewport");
    if (!canvas || !viewport) return;
    var scale = Math.min(viewport.clientWidth / 1280, viewport.clientHeight / 720);
    canvas.style.transform = "scale(" + scale + ")";
  }

  /* ------------------------------------------------------------------
     상단 바 페이지 번호 자동 기입
     ------------------------------------------------------------------ */
  function fillTopbarPage() {
    var el = document.querySelector(".topbar-page");
    if (el) el.textContent = (getCurrentIndex() + 1) + " / " + SLIDES.length;
  }

  /* ------------------------------------------------------------------
     프로그레스 바 (최하단 5px)
     ------------------------------------------------------------------ */
  function buildProgressBar() {
    if (document.querySelector(".progress-track")) return;
    var track = document.createElement("div");
    track.className = "progress-track";
    var fill = document.createElement("div");
    fill.className = "progress-fill";
    var pct = ((getCurrentIndex() + 1) / SLIDES.length) * 100;
    fill.style.width = pct + "%";
    track.appendChild(fill);
    var host = document.querySelector(".slide-canvas") || document.body;
    host.appendChild(track);
  }

  /* ------------------------------------------------------------------
     전체화면 (Fullscreen API, 벤더 접두사 포함)

     주의: 슬라이드 이동은 매번 새 문서를 로드한다(file:// 호환을 위해
     SPA 구조를 쓰지 않음). 브라우저는 보안상 "탭/프레임이 이 방식으로
     탐색(navigate)하면" — 최상위 문서든 iframe이든 — 전체화면을 자동
     해제한다(전체화면 상태에서 몰래 다른 화면으로 바꿔치기하는 걸
     막기 위한 정책). 즉 이 F/버튼 전체화면은 슬라이드 1장 안에서만
     유지되고, 다음/이전 슬라이드로 넘어가면 자동으로 풀린다.
     슬라이드를 넘겨도 안 풀리는 진짜 "풀스크린"이 필요하면 브라우저
     자체 전체화면(F11)을 쓴다 — 그건 창(브라우저) 단위 기능이라 페이지
     탐색과 무관하게 유지된다.
     ------------------------------------------------------------------ */
  function isFullscreen() {
    return !!(
      document.fullscreenElement ||
      document.webkitFullscreenElement ||
      document.msFullscreenElement
    );
  }

  function enterFullscreen() {
    var el = document.documentElement;
    var req =
      el.requestFullscreen ||
      el.webkitRequestFullscreen ||
      el.msRequestFullscreen;
    if (!req) return;
    var result = req.call(el);
    if (result && typeof result.catch === "function") {
      result.catch(function () {});
    }
  }

  function exitFullscreen() {
    var exit =
      document.exitFullscreen ||
      document.webkitExitFullscreen ||
      document.msExitFullscreen;
    if (exit) exit.call(document);
  }

  function toggleFullscreen() {
    if (isFullscreen()) exitFullscreen();
    else enterFullscreen();
  }

  function updateFullscreenBtn() {
    var btn = document.querySelector(".nav-fullscreen");
    if (!btn) return;
    if (isFullscreen()) {
      btn.innerHTML = "&#x2715;";
      btn.title = "전체화면 종료 (F)";
    } else {
      btn.innerHTML = "&#x26F6;";
      btn.title = "전체화면 (F)";
    }
  }

  /* ------------------------------------------------------------------
     우하단 플로팅 내비 컨트롤
     ------------------------------------------------------------------ */
  function buildNavControls() {
    if (document.querySelector(".nav-controls")) return;
    var idx = getCurrentIndex();

    var wrap = document.createElement("div");
    wrap.className = "nav-controls";

    var prevBtn = document.createElement("button");
    prevBtn.className = "nav-btn nav-prev";
    prevBtn.innerHTML = "&#8249;";
    prevBtn.title = "이전 슬라이드 (←)";
    prevBtn.disabled = idx === 0;
    prevBtn.addEventListener("click", goPrev);

    var indicator = document.createElement("div");
    indicator.className = "nav-page-indicator";
    indicator.textContent = (idx + 1) + " / " + SLIDES.length;
    indicator.title = "목차 열기 (T)";
    indicator.addEventListener("click", toggleToc);

    var nextBtn = document.createElement("button");
    nextBtn.className = "nav-btn nav-next";
    nextBtn.innerHTML = "&#8250;";
    nextBtn.title = "다음 슬라이드 (→)";
    nextBtn.disabled = idx === SLIDES.length - 1;
    nextBtn.addEventListener("click", goNext);

    var fsBtn = document.createElement("button");
    fsBtn.className = "nav-btn nav-fullscreen";
    fsBtn.addEventListener("click", toggleFullscreen);

    wrap.appendChild(prevBtn);
    wrap.appendChild(indicator);
    wrap.appendChild(nextBtn);
    wrap.appendChild(fsBtn);

    var host = document.querySelector(".slide-canvas") || document.body;
    host.appendChild(wrap);
    updateFullscreenBtn();
  }

  /* ------------------------------------------------------------------
     목차 오버레이
     ------------------------------------------------------------------ */
  function buildTocOverlay() {
    if (document.querySelector(".toc-overlay")) return;

    var overlay = document.createElement("div");
    overlay.className = "toc-overlay";
    overlay.id = "tocOverlay";

    var panel = document.createElement("div");
    panel.className = "toc-panel";

    var header = document.createElement("div");
    header.className = "toc-panel-header";
    header.innerHTML =
      "<h2>목차</h2>" +
      '<span class="toc-hint">T 또는 Esc로 닫기 &middot; 카드 클릭으로 이동</span>';

    var grid = document.createElement("div");
    grid.className = "toc-grid";

    var curIdx = getCurrentIndex();
    SLIDES.forEach(function (slide, i) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "toc-item" + (i === curIdx ? " current" : "");
      item.innerHTML =
        '<span class="toc-num">' + (i + 1) + "</span>" +
        '<span class="toc-title">' + slide.title + "</span>";
      item.addEventListener("click", function () { goTo(i); });
      grid.appendChild(item);
    });

    panel.appendChild(header);
    panel.appendChild(grid);
    overlay.appendChild(panel);

    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) closeToc();
    });

    document.body.appendChild(overlay);
  }

  function toggleToc() {
    var overlay = document.getElementById("tocOverlay");
    if (overlay) overlay.classList.toggle("open");
  }

  function closeToc() {
    var overlay = document.getElementById("tocOverlay");
    if (overlay) overlay.classList.remove("open");
  }

  function isTocOpen() {
    var overlay = document.getElementById("tocOverlay");
    return !!overlay && overlay.classList.contains("open");
  }

  /* ------------------------------------------------------------------
     키보드 내비게이션
     (↑/↓ 는 바인딩하지 않음 — 슬라이드 내부 세로 스크롤용)
     ------------------------------------------------------------------ */
  function handleKeydown(e) {
    switch (e.key) {
      case "ArrowRight":
      case " ":
      case "Spacebar":
      case "PageDown":
        e.preventDefault();
        if (!isTocOpen()) goNext();
        break;
      case "ArrowLeft":
      case "PageUp":
        e.preventDefault();
        if (!isTocOpen()) goPrev();
        break;
      case "Home":
        e.preventDefault();
        if (!isTocOpen()) goFirst();
        break;
      case "End":
        e.preventDefault();
        if (!isTocOpen()) goLast();
        break;
      case "t":
      case "T":
        e.preventDefault();
        toggleToc();
        break;
      case "f":
      case "F":
        e.preventDefault();
        toggleFullscreen();
        break;
      case "Escape":
        e.preventDefault();
        if (isTocOpen()) closeToc();
        else toggleToc();
        break;
      default:
        break;
    }
  }

  function init() {
    fitCanvas();
    fillTopbarPage();
    buildProgressBar();
    buildNavControls();
    buildTocOverlay();
    window.addEventListener("resize", fitCanvas);
    document.addEventListener("keydown", handleKeydown);
    ["fullscreenchange", "webkitfullscreenchange", "msfullscreenchange"].forEach(
      function (evt) {
        document.addEventListener(evt, function () {
          fitCanvas();
          updateFullscreenBtn();
        });
      }
    );
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  /* 전역 노출 */
  window.HomeShieldNav = {
    SLIDES: SLIDES,
    goTo: goTo,
    goNext: goNext,
    goPrev: goPrev,
    goFirst: goFirst,
    goLast: goLast,
    toggleToc: toggleToc,
    toggleFullscreen: toggleFullscreen,
    getCurrentIndex: getCurrentIndex
  };
})();
