type Props = {
  title?: React.ReactNode;
  hint?: React.ReactNode;
  actions?: React.ReactNode;
  as?: "section" | "aside";
  className?: string;
  id?: string;
  children: React.ReactNode;
};

export default function Card({ title, hint, actions, as: Tag = "section", className, id, children }: Props) {
  return (
    <Tag id={id} className={className ? `card ${className}` : "card"}>
      {(title || actions) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {hint && <span className="card-hint">{hint}</span>}
          {actions && <div className="card-actions">{actions}</div>}
        </div>
      )}
      {children}
    </Tag>
  );
}
