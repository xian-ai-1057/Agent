/** 「思考中」：三個輕輕閃動的點。 */

export function Thinking({ label = "思考中…" }: { label?: string }) {
  return (
    <div className="thinking ch-thinking" role="status">
      <span className="ch-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      {label}
    </div>
  );
}
