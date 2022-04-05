allow(actor, action, resource) if
  has_permission(actor, action, resource);

# Users can see each other.
has_permission(_: User, "read", _: User);

# A User can read their own profile.
has_permission(_: User{id: id}, "read_profile", _:User{id: id});

# Any logged-in user can create a new group.
has_permission(_: User, "create", _: Group);

actor User {
  permissions = ["read"];
}

resource Group {
  roles = ["owner", "member"];
  permissions = [
    "read",
    "write",
  ];

  "read" if "member";
  "write" if "owner";

  "member" if "owner";
}

has_role(user: User, name: String, group: Group) if
    role in user.group_roles and
    role.role.name = name and
    role.group_id = group.id;

resource Sample {
  roles = ["reader", "writer"];
  permissions = [
    "read",
    "write",
  ];
  relations = { parent: Group };

  "read" if "reader";
  "write" if "writer";

  "writer" if "owner" on "parent";
  "reader" if "member" on "parent";
}
has_relation(group: Group, "parent", sample: Sample) if sample.submitting_group = group;

resource PhyloRun {
  roles = ["reader", "writer"];
  permissions = [
    "read",
    "write",
  ];
  relations = { parent: Group };

  "read" if "reader";
  "write" if "writer";

  "writer" if "owner" on "parent";
  "reader" if "owner" on "parent";
  "reader" if "member" on "parent";
}

has_relation(group: Group, "parent", phylo_run: PhyloRun) if phylo_run.group = group;

resource PhyloTree {
  roles = ["member", "owner"];
  permissions = [
    "read",
    "write",
  ];

  "read" if "member";
  "read" if "owner";
  "write" if "owner";
}


has_role(user: User, name: String, phylo_tree: PhyloTree) if
    role in user.group_roles and
    role.group_id = phylo_tree.phylo_run.group_id and
    role.role.name = name;

resource GroupRole {
  permissions = ["read"];
  relations = { group: Group };
}

has_relation(group: Group, "group", role: GroupRole) if group = role.group;
