export async function renderSafeDocxPreview(
  blob: Blob,
  container: HTMLElement,
  className: string,
): Promise<void> {
  const { renderAsync } = await import("docx-preview");
  container.replaceChildren();
  await renderAsync(blob, container, undefined, {
    className,
    inWrapper: true,
    ignoreWidth: false,
    ignoreHeight: false,
    renderAltChunks: false,
    renderChanges: false,
    renderComments: false,
  });

  container.querySelectorAll("script, iframe, object, embed").forEach((node) => node.remove());
  container.querySelectorAll<HTMLAnchorElement>("a").forEach((link) => {
    link.removeAttribute("href");
    link.removeAttribute("target");
    link.setAttribute("aria-disabled", "true");
    link.title = "浏览器预览中已禁用外部链接";
  });
}
