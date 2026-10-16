type Props = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary";
  // A square button holding only an icon; it needs an aria-label.
  icon?: boolean;
};

export default function Button({ variant = "primary", icon = false, className, type = "button", ...rest }: Props) {
  const classes = ["btn", `btn-${variant}`, icon && "btn-icon", className].filter(Boolean).join(" ");
  return <button type={type} className={classes} {...rest} />;
}
