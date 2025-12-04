import re
from math import ceil

import dateparser

from aspen.database.models import TreeType
from aspen.workflows.nextstrain_run.build_plugins.base_plugin import BaseConfigPlugin


class TreeTypePlugin(BaseConfigPlugin):
    crowding_penalty: float = 0
    tree_type: TreeType
    subsampling_scheme: str = "NONE"

    def _update_config_params(self, config):
        if not config.get("builds"):
            # TODO, force MPX structure to look more like SC2's
            config["builds"] = {"aspen": {}}
        build = config["builds"]["aspen"]

        location = self.template_args["location"]
        # Make a shortcut to decide whether this is a location vs division vs country level build
        if not location.division:
            self.tree_build_level = "country"
        elif not location.location:
            self.tree_build_level = "division"
        else:
            self.tree_build_level = "location"
        build["location"] = str(location.location)
        build["division"] = str(location.division)
        build["country"] = str(location.country)
        build["geo_group"] = location.geo_group

        target_crowding = self.crowding_penalty * (
            self.template_args.get("target_crowding", 2)
        )
        if target_crowding:
            build["target_crowding"] = target_crowding

        build["subsampling_scheme"] = self.subsampling_scheme

        if self.template_args.get("filter_pango_lineages"):
            build["filter_pango_lineages"] = self.template_args["filter_pango_lineages"]

        # Filter samples by date. Ex: samples from the past 6 months.
        # This is specific to the "aspen" build only.
        build["filter_start_date"] = self.template_args.get("filter_start_date")

        # Cherry-pick specific samples (aka "ids")
        if self.template_args.get("filter_include_ids"):
            build["include_ids"] = self.template_args["filter_include_ids"]

        # Filter samples by lineage. This is specific to the "aspen" build only.
        if self.template_args.get("filter_pango_lineages"):
            build["filter_pango_lineages"] = self.template_args["filter_pango_lineages"]

        # Filter samples by region. This is specific to the "aspen" build only.
        if self.template_args.get("filter_regions"):
            build["filter_regions"] = self.template_args["filter_regions"]

        # Filter samples by country. This is specific to the "aspen" build only.
        if self.template_args.get("filter_countries"):
            build["filter_countries"] = self.template_args["filter_countries"]

        # Filter samples by division. This is specific to the "aspen" build only.
        if self.template_args.get("filter_divisions"):
            build["filter_divisions"] = self.template_args["filter_divisions"]


class OverviewPlugin(TreeTypePlugin):
    crowding_penalty = 1.0
    tree_type = TreeType.OVERVIEW
    subsampling_scheme = "OVERVIEW"

    def run_type_config(self, config, subsampling):
        """
        DATA we can use in this function:
          config : the entire mega-template data structure, with build configs already updated by BaseNextstrainConfigBuilder.update_build()
          subsampling : the subsampling scheme for *this build type only* (ex: mega_template["subsampling"]["OVERVIEW"])
          self.subsampling_scheme : the value a few lines above
          self.crowding_penalty : the value a few lines above
          self.group : information about the group that this run is for (ex: self.group.name or self.group.default_tree_location)
          self.num_sequences : the number of aspen samples written to our fasta input file
          self.num_included_samples : the number of samples in include.txt (aspen + gisaid samples) for on-demand runs only

        EXAMPLES SECTION:
          Delete a group from a subsampling scheme:
              del subsampling["international"]

          Delete a setting from a group:
              del subsampling["international"]["seq_per_group"]

          Add a group to a subsampling scheme:
              subsampling["my_new_group_name"] = {
                  "group_by": "region",
                  "max_sequences": 200,
                  "query": '--query "(foo != {bar})"'
              }

          Add a setting to a group (this is the same as updating an existing setting!):
              subsampling["international"]["mynewsetting"] = "mynewvalue"
        """
        # Adjust group sizes if we have a lot of samples.
        root_max_sequences = 25
        state_max_sequences = 500
        country_max_sequences = 400
        international_max_sequences = 100
        if self.num_included_samples >= 250:
            # For huge builds, just show the root, the local group, and some
            # serial-sampling implemented in the subsampling config.
            del subsampling["state"]
            del subsampling["country"]
            del subsampling["international"]

        if self.template_args.get("target_crowding"):
            # The crowding penalty increases the target number of sequences to
            # prevent high sample density areas from over-crowding the tree
            crowding_penalty = self.crowding_penalty
            root_max_sequences *= crowding_penalty
            country_max_sequences *= crowding_penalty
            state_max_sequences *= crowding_penalty
            international_max_sequences *= crowding_penalty

        subsampling["root"]["max_sequences"] = root_max_sequences
        subsampling["state"]["max_sequences"] = state_max_sequences
        subsampling["country"]["max_sequences"] = country_max_sequences
        subsampling["international"]["max_sequences"] = international_max_sequences

        # Handle sampling date & pango lineage filters
        apply_filters(config, subsampling, self.template_args)

        # Links the location, division, and country for country and division level
        # sampling.
        update_subsampling_for_location(self.tree_build_level, subsampling)


class NonContextualizedPlugin(TreeTypePlugin):
    crowding_penalty = 0
    tree_type = TreeType.NON_CONTEXTUALIZED
    subsampling_scheme = "NON_CONTEXTUALIZED"

    def run_type_config(self, config, subsampling):
        """
        DATA we can use in this function:
          config : the entire mega-template data structure, with build configs already updated by BaseNextstrainConfigBuilder.update_build()
          subsampling : the subsampling scheme for *this build type only* (ex: mega_template["subsampling"]["NON_CONTEXTUALIZED"])
          self.subsampling_scheme : the value a few lines above
          self.crowding_penalty : the value a few lines above
          self.group : information about the group that this run is for (ex: self.group.name or self.group.default_tree_location)
          self.num_sequences : the number of aspen samples written to our fasta input file
          self.num_included_samples : the number of samples in include.txt (aspen + gisaid samples) for on-demand runs only

        EXAMPLES SECTION:
          Delete a group from a subsampling scheme:
              del subsampling["group"]

          Delete a setting from a group:
              del subsampling["group"]["max_sequences"]

          Add a group to a subsampling scheme:
              subsampling["my_new_group_name"] = {
                  "group_by": "region",
                  "max_sequences": 200,
                  "query": '--query "(foo != {bar})"'
              }

          Add a setting to a group (this is the same as updating an existing setting!):
              subsampling["group"]["mynewsetting"] = "mynewvalue"
        """
        # Handle sampling date & pango lineage filters
        apply_filters(config, subsampling, self.template_args)

        # This is a strict target for the number of sequences.
        subsampling["group"]["max_sequences"] = 1500 * self.crowding_penalty


class TargetedPlugin(TreeTypePlugin):
    crowding_penalty = 0
    tree_type = TreeType.TARGETED
    subsampling_scheme = "TARGETED"

    def run_type_config(self, config, subsampling):
        """
        DATA we can use in this function:
          config : the entire mega-template data structure, with build configs already updated by BaseNextstrainConfigBuilder.update_build()
          subsampling : the subsampling scheme for *this build type only* (ex: mega_template["subsampling"]["TARGETED"])
          self.subsampling_scheme : the value a few lines above
          self.crowding_penalty : the value a few lines above
          self.group : information about the group that this run is for (ex: self.group.name or self.group.default_tree_location)
          self.num_sequences : the number of aspen samples written to our fasta input file
          self.num_included_samples : the number of samples in include.txt (aspen + gisaid samples) for on-demand runs only

        EXAMPLES SECTION:
          Delete a group from a subsampling scheme:
              del subsampling["international"]

          Delete a setting from a group:
              del subsampling["international"]["seq_per_group"]

          Add a group to a subsampling scheme:
              subsampling["my_new_group_name"] = {
                  "group_by": "region",
                  "max_sequences": 200,
                  "query": '--query "(foo != {bar})"'
              }

          Add a setting to a group (this is the same as updating an existing setting!):
              subsampling["international"]["mynewsetting"] = "mynewvalue"
        """
        # Adjust group sizes if we have a lot of samples.
        closest_max_sequences = 250
        other_max_sequences = 25
        if self.num_included_samples >= 250:
            # For huge builds, just show the root, the local group, and some
            # serial-sampling implemented in the subsampling config.
            closest_max_sequences = 250
            other_max_sequences = 100
        elif self.num_included_samples >= 100:
            closest_max_sequences = 250
            other_max_sequences = 50
        elif self.num_included_samples >= 50:
            closest_max_sequences = 200
            other_max_sequences = 25
        elif self.num_included_samples >= 25:
            closest_max_sequences = 150
            other_max_sequences = 25
        elif self.num_included_samples >= 10:
            closest_max_sequences = 100
            other_max_sequences = 25
        else:
            # Make sure we have enough context to make the tree's structure meaningful
            closest_max_sequences = 50
            other_max_sequences = 25

        if self.num_included_samples > 1000:
            # A large number of samples in include.txt indicates that gisaid might include many nearly-identical samples.
            # Decrease the number of sequences selected by the "closest" group to improve performance and reduce potential bias from including too many closely related gisaid samples.
            factor = 1000 / float(self.num_included_samples)
            closest_max_sequences = int(ceil(closest_max_sequences * factor))
            other_max_sequences = int(ceil(other_max_sequences * factor))

        subsampling["closest"]["max_sequences"] = closest_max_sequences

        subsampling["group"]["max_sequences"] = (
            other_max_sequences * 2
        )  # Temp mitigation for missing on-demand overview
        subsampling["state"]["max_sequences"] = (
            other_max_sequences * 2
        )  # Temp mitigation for missing on-demand overview
        subsampling["country"]["max_sequences"] = other_max_sequences
        subsampling["international"]["max_sequences"] = other_max_sequences

        # Handle sampling date and lineage filters for targeted builds
        apply_filters(config, subsampling, self.template_args)

        # Update our sampling for state/country level builds if necessary
        update_subsampling_for_location(self.tree_build_level, subsampling)


def update_subsampling_for_location(tree_build_level, subsampling):
    # location "state" means we want this to be a location level build.
    # we should change the "division" query to use location
    if tree_build_level == "location":
        subsampling["group"]["query"] = subsampling["group"]["query"].replace(
            "division",
            "location",
        )

        subsampling["state"]["query"] = subsampling["state"]["query"].replace(
            "_state",
            "_location",
        )

        # state-group is group, country-group becomes state-group
        subsampling["group"] = subsampling["state"]
        subsampling["state"] = subsampling["country"]

        # and country-group becomes international
        subsampling["country"] = subsampling["international"]
        del subsampling["international"]

    # location division means we are making a division level build, and should omit the
    # country layer
    elif tree_build_level == "division":
        del subsampling["country"]


def apply_filters(config, subsampling, template_args):
    """NOTE: The filters do NOT currently support filtering MPX by lineage.

    It probably would not be a big lift to change the config builder to support
    lineage filtering on mpox, but it's unclear if that should happen here in the
    tree type plugins where we handle the rest of the filters, or somehow get
    shoehorned into the pathogen plugins instead.
    Regardless, the filters currently only support "pango_lineage" for SC2 lineage
    filtering. There is currently no FE or BE code to provide lineage filter support
    for mpox, and that's where most of the eng effort will be if we want to release
    mpox lineage filtering in the future. Once the user has a way to pass those params
    to filter mpox trees using lineage though, it will still be necessary to change
    the config building process here so lineage filter is correctly handled for mpox
    and integrates with the downstream snakemake workflow that builds the tree.
    """

    # ---- Date filters ----
    min_date = template_args.get("filter_start_date")
    if min_date:
        # Support date expressions like "5 days ago" in our cron schedule.
        min_date = dateparser.parse(min_date).strftime("%Y-%m-%d")
        subsampling["group"]["min_date"] = f"--min-date {min_date}"

    max_date = template_args.get("filter_end_date")
    if max_date:
        # Support date expressions like "5 days ago" in our cron schedule.
        max_date = dateparser.parse(max_date).strftime("%Y-%m-%d")
        subsampling["group"]["max_date"] = f"--max-date {max_date}"
        if "international_serial_sampling" in subsampling:
            subsampling["international_serial_sampling"]["max_date"] = f"--max-date {max_date}"

    # ---- Lineage filters (SC2 only, extended) ----
    LINEAGE_FIELD = "pango_lineage"
    pango_lineages = template_args.get("filter_pango_lineages")
    if not pango_lineages:
        return

    # Nextstrain is rather particular about the acceptable syntax for
    # values in the pango_lineages key. Before modifying please see
    # https://discussion.nextstrain.org/t/failure-when-specifying-multiple-pango-lineages-in-a-build/670
    clean_values = [re.sub(r"[^0-9a-zA-Z.]", "", item) for item in pango_lineages]
    clean_values.sort()
    config["builds"]["aspen"]["pango_lineage"] = clean_values

    # Build the lineage query fragment
    lineage_query = f" & ({LINEAGE_FIELD} in {{pango_lineage}})"

    def _add_lineage_to_block(block_name: str) -> None:
        if block_name not in subsampling:
            return
        old_query = subsampling[block_name]["query"]
        end_string = ""
        # Preserve any trailing quote, as in the original implementation
        if old_query.endswith('"'):
            end_string = '"'
            old_query = old_query[:-1]
        subsampling[block_name]["query"] = old_query + lineage_query + end_string

    # 1) Original behavior: group is lineage-filtered
    _add_lineage_to_block("group")

    # 2) New behavior: same-lineage context blocks
    _add_lineage_to_block("international_same_lineage")