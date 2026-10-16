// Decorative unless given a label; the surrounding text usually says what is loading.
export default function Spinner({ label }: { label?: string }) {
  return label
    ? <span className="spinner" role="status" aria-label={label} />
    : <span className="spinner" aria-hidden="true" />;
}
