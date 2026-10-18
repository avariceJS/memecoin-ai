export type DomSample = { kind: "escape" | "overlap" | "clip"; text: string };

export type ShellCounts = {
  overflow_px: number;
  text_overlap_count: number;
  card_escape_count: number;
  unintended_clip_count: number;
};

export type DomMeasurement = {
  page_overflow_px: number;
  text_overlap_count: number;
  card_escape_count: number;
  unintended_clip_count: number;
  page_overflow_hidden: boolean;
  allowed_scroll_containers: string[];
  inspected_regions: string[];
  shell_counts: ShellCounts;
  samples: DomSample[];
};

export function measureDom(): DomMeasurement {
  const EPS = 2;
  const roundPx = (value: number) => Math.round(value * 100) / 100;
  const samples: DomSample[] = [];
  const remember = (kind: DomSample["kind"], text: string) => {
    if (samples.length < 12) {
      samples.push({ kind, text: text.slice(0, 120) });
    }
  };

  const rootStyle = getComputedStyle(document.documentElement);
  const bodyStyle = getComputedStyle(document.body);
  const pageOverflowHidden = [rootStyle.overflowX, bodyStyle.overflowX].some(
    (value) => value === "hidden" || value === "clip",
  );
  const pageOverflow = roundPx(
    Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
  );

  type Box = {
    left: number;
    top: number;
    right: number;
    bottom: number;
    text: string;
    parentId: number;
    shell: boolean;
    container: DOMRect | null;
    scrolled: boolean;
  };

  const parentIds = new WeakMap<Element, number>();
  let nextParent = 1;
  const parentId = (element: Element) => {
    const existing = parentIds.get(element);
    if (existing) {
      return existing;
    }
    parentIds.set(element, nextParent);
    nextParent += 1;
    return nextParent - 1;
  };

  const boxes: Box[] = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let current = walker.nextNode();
  while (current) {
    const text = (current.textContent ?? "").replace(/\s+/g, " ").trim();
    const parent = current.parentElement;
    if (text && parent) {
      const style = getComputedStyle(parent);
      const shown = style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
      if (shown) {
        const range = document.createRange();
        range.selectNodeContents(current);
        const shell = parent.closest("main") === null;
        const containerEl = shell
          ? parent.closest("header, aside")
          : parent.closest("[data-testid^='d48-cell'], [data-cc-contain], [data-cc-panel], section, article");
        let scrolled = false;
        if (containerEl) {
          let node: Element | null = parent;
          while (node) {
            const nodeStyle = getComputedStyle(node);
            if (nodeStyle.overflowX !== "visible" || nodeStyle.overflowY !== "visible") {
              scrolled = true;
              break;
            }
            if (node === containerEl) {
              break;
            }
            node = node.parentElement;
          }
        }
        const container = containerEl?.getBoundingClientRect() ?? null;
        for (const rect of range.getClientRects()) {
          if (rect.width >= 1 && rect.height >= 1) {
            boxes.push({
              left: rect.left,
              top: rect.top,
              right: rect.right,
              bottom: rect.bottom,
              text,
              parentId: parentId(parent),
              shell,
              container,
              scrolled,
            });
          }
        }
      }
    }
    current = walker.nextNode();
  }

  let textOverlap = 0;
  let shellOverlap = 0;
  const buckets = new Map<number, number[]>();
  boxes.forEach((box, index) => {
    const key = Math.floor(box.top / 8);
    buckets.set(key, [...(buckets.get(key) ?? []), index]);
  });
  const seen = new Set<string>();
  for (const [key, indices] of buckets) {
    const neighborhood = [...indices, ...(buckets.get(key + 1) ?? [])];
    for (let left = 0; left < neighborhood.length; left += 1) {
      for (let right = left + 1; right < neighborhood.length; right += 1) {
        const a = boxes[neighborhood[left]];
        const b = boxes[neighborhood[right]];
        if (!a || !b || a.parentId === b.parentId) {
          continue;
        }
        const pairKey = neighborhood[left] < neighborhood[right]
          ? `${neighborhood[left]}:${neighborhood[right]}`
          : `${neighborhood[right]}:${neighborhood[left]}`;
        if (seen.has(pairKey)) {
          continue;
        }
        seen.add(pairKey);
        const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (width > EPS && height > EPS) {
          textOverlap += 1;
          if (a.shell && b.shell) {
            shellOverlap += 1;
          }
          remember("overlap", `${a.text} ∩ ${b.text}`);
        }
      }
    }
  }

  let cardEscape = 0;
  let shellEscape = 0;
  for (const box of boxes) {
    if (!box.container || box.scrolled) {
      continue;
    }
    const escaped =
      box.left < box.container.left - EPS ||
      box.right > box.container.right + EPS ||
      box.top < box.container.top - EPS ||
      box.bottom > box.container.bottom + EPS;
    if (!escaped) {
      continue;
    }
    cardEscape += 1;
    if (box.shell) {
      shellEscape += 1;
    }
    remember("escape", box.text);
  }

  let clip = 0;
  let shellClip = 0;
  const allowedScroll: string[] = [];
  for (const element of document.querySelectorAll("body *")) {
    if (!(element instanceof HTMLElement) || element.clientWidth < 2 || element.clientHeight < 2) {
      continue;
    }
    const style = getComputedStyle(element);
    const clipsX = style.overflowX === "hidden" || style.overflowX === "clip";
    const clipsY = style.overflowY === "hidden" || style.overflowY === "clip";
    const scrollsX = style.overflowX === "auto" || style.overflowX === "scroll";
    const scrollsY = style.overflowY === "auto" || style.overflowY === "scroll";
    if (!clipsX && !clipsY && !scrollsX && !scrollsY) {
      continue;
    }
    if (element.hasAttribute("data-cc-scroll")) {
      allowedScroll.push(element.getAttribute("data-cc-scroll") || element.tagName.toLowerCase());
      continue;
    }
    const overflowX = element.scrollWidth > element.clientWidth + EPS;
    const overflowY = element.scrollHeight > element.clientHeight + EPS;
    const shell = element.closest("main") === null;
    const shellScroll = shell && (element.closest("aside") !== null || element.tagName === "HEADER");
    const ellipsis =
      style.textOverflow === "ellipsis" &&
      (element.hasAttribute("title") || element.hasAttribute("aria-label"));
    const clipped = (clipsX && overflowX && !ellipsis) || (clipsY && overflowY && !ellipsis);
    const unnamedScroll = (scrollsX && overflowX) || (scrollsY && overflowY);
    if (clipped || (unnamedScroll && !shellScroll)) {
      clip += 1;
      if (shell) {
        shellClip += 1;
      }
      remember("clip", (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim());
    }
  }

  let shellOverflow = 0;
  for (const element of [document.querySelector("aside"), document.querySelector("header")]) {
    if (!element) {
      continue;
    }
    const rect = element.getBoundingClientRect();
    shellOverflow = Math.max(shellOverflow, rect.right - window.innerWidth, -rect.left);
  }

  const regions: string[] = [];
  const addRegion = (name: string, found: Element | null) => {
    if (found) {
      regions.push(name);
    }
  };
  addRegion("sidebar", document.querySelector("aside"));
  addRegion("header", document.querySelector("header"));
  addRegion("main-grid", document.querySelector("main"));
  addRegion("shell-containment", document.querySelector("#root"));
  addRegion("d48-grid", document.querySelector("[data-testid='d48-plan-grid']"));
  for (const element of document.querySelectorAll("[data-cc-region]")) {
    const name = element.getAttribute("data-cc-region");
    if (name && !regions.includes(name)) {
      regions.push(name);
    }
  }

  return {
    page_overflow_px: pageOverflow,
    text_overlap_count: textOverlap,
    card_escape_count: cardEscape,
    unintended_clip_count: clip,
    page_overflow_hidden: pageOverflowHidden,
    allowed_scroll_containers: allowedScroll,
    inspected_regions: regions,
    shell_counts: {
      overflow_px: roundPx(Math.max(0, shellOverflow)),
      text_overlap_count: shellOverlap,
      card_escape_count: shellEscape,
      unintended_clip_count: shellClip,
    },
    samples,
  };
}
