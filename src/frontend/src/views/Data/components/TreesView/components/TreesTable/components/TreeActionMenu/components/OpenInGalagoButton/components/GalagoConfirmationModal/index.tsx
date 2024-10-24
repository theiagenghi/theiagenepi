import {
  AnalyticsTreeViewGalago,
  EVENT_TYPES,
} from "src/common/analytics/eventTypes";
import { analyticsTrackEvent } from "src/common/analytics/methods";
import galagoLogo from "src/common/images/galago-logo-beta.png";
import { ConfirmButton } from "src/views/Data/components/ConfirmButton";
import { RedirectConfirmationModal } from "src/views/Data/components/RedirectConfirmationModal";

interface Props {
  open: boolean;
  onClose: () => void;
  treeId: number;
}

export const GalagoConfirmationModal = ({
  open,
  onClose,
  treeId,
}: Props): JSX.Element => {
  const content = (
    <>
      By clicking “Continue” you agree to send a copy of your tree JSON to
      Galago (Beta), a separate, but related service from TheiaGenEpi. Galago is
      a serverless web application which runs entirely in the browser. Galago
      does not store or share your data; however, you may choose to share the
      URL with others.
    </>
  );

  const confirmButton = (
    <ConfirmButton
      treeId={treeId}
      outgoingDestination="galago"
      onClick={() =>
        analyticsTrackEvent<AnalyticsTreeViewGalago>(
          EVENT_TYPES.TREE_VIEW_GALAGO,
          {
            tree_id: treeId,
          }
        )
      }
    />
  );

  return (
    <RedirectConfirmationModal
      content={content}
      customConfirmButton={confirmButton}
      img={galagoLogo}
      isOpen={open}
      onClose={onClose}
      onConfirm={onClose}
      logoWidth={180}
    />
  );
};
