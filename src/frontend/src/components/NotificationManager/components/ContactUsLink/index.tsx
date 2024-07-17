import { NewTabLink } from "src/common/components/library/NewTabLink";

const ContactUsLink = (): JSX.Element => (
  <span>
    Please try again later or{" "}
    <NewTabLink href="mailto:support@theiagenghi.org" sdsStyle="dashed">
      contact us
    </NewTabLink>{" "}
    for help.
  </span>
);

export { ContactUsLink };
