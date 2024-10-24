export enum ROUTES {
  THEIAGENGHI_URL = "https://www.theiagenghi.org/",
  UPLOAD_STEP_BASE = "/upload/step",
  HELP_CENTER_URL = "https://help.theiagenepi.org",

  HOMEPAGE = "/",
  ACCOUNT = "/account",
  AGREE_TERMS = "/agreeTerms",
  BIOHUB = THEIAGENGHI_URL,
  CAREERS = THEIAGENGHI_URL,
  CONTACT_US_EMAIL = "mailto:support@theiagenghi.org",
  CZI = THEIAGENGHI_URL,
  DATA = "/data",
  DATA_SAMPLES = "/data/samples",
  GALAGO = "https://galago.theiagenepi.org/",
  GISAID = "https://www.gisaid.org/",
  GITHUB = "https://github.com/theiagenghi/theiagenepi",
  GROUP = "/group",
  GROUP_DETAILS = "/group/details",
  GROUP_INVITATIONS = "/group/members/invitations",
  GROUP_MEMBERS = "/group/members",
  HELP_CENTER = HELP_CENTER_URL,
  NCBI_VIRUS = "https://www.ncbi.nlm.nih.gov/labs/virus/vssi/#/",
  NEXTCLADE = "https://clades.nextstrain.org/",
  NEXTSTRAIN = "https://nextstrain.org/",
  PANGOLIN = "https://pangolin.cog-uk.io/",
  PHYLO_TREES = "/data/phylogenetic_trees",
  PRIVACY = "/privacy",
  PRIVACY_DATA_COLLECTION = "/privacy#privacy-data-collection",
  PRIVACY_DATA_SHARING_FAQ = HELP_CENTER_URL,
  REQUEST_ACCESS = "https://forms.gle/cdHv5nZcs3Xwd5uB7",
  RESOURCES = HELP_CENTER_URL,
  TERMS = "/terms",
  UPLOAD = "/upload",
  UPLOAD_STEP1 = "/upload/step/1",
  UPLOAD_STEP2 = "/upload/step/2",
  UPLOAD_STEP3 = "/upload/step/3",
  USHER = "https://genome.ucsc.edu/cgi-bin/hgPhyloPlace",
}

export const publicPaths: string[] = [
  ROUTES.HOMEPAGE,
  ROUTES.PRIVACY,
  ROUTES.TERMS,
];

export const workspacePaths: string[] = [
  ROUTES.DATA,
  ROUTES.DATA_SAMPLES,
  ROUTES.PHYLO_TREES,
  ROUTES.UPLOAD,
];
