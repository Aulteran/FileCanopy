import { ADMIN_PATH } from "./route";

/** N07: a way back from an admin sub-page to the Administration cards. */
export function AdminCrumb({
  page,
  go,
}: {
  page: string;
  go: (path: string) => void;
}) {
  return (
    <nav aria-label="Breadcrumb" className="crumbs admin-crumb">
      <ol>
        <li>
          <a
            href={ADMIN_PATH}
            onClick={(e) => {
              e.preventDefault();
              go(ADMIN_PATH);
            }}
          >
            Administration
          </a>
        </li>
        <li aria-current="page">{page}</li>
      </ol>
    </nav>
  );
}
