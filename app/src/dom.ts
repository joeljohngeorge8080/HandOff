// Tiny DOM helper. Text is always set via textContent: file and device names come from the
// network or the filesystem and must never be parsed as HTML.
export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: { class?: string; text?: string; title?: string; disabled?: boolean; type?: string } = {},
  ...children: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (props.class) node.className = props.class;
  if (props.text !== undefined) node.textContent = props.text;
  if (props.title) node.title = props.title;
  if (props.disabled !== undefined) (node as HTMLButtonElement).disabled = props.disabled;
  if (props.type) node.setAttribute("type", props.type);
  for (const c of children) node.append(c);
  return node;
}
