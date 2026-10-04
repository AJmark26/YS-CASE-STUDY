"""Drift correction for long LiDAR captures: chunked pose graph with ICP loop closures.

ARKit odometry is locally excellent but accumulates drift over a 50 m walk, and it can
relocalise mid-capture (a sudden pose jump). Both show up as doubled walls in the plan.

Method
1. Split the trajectory into chunks of ~`chunk_s` seconds, also cutting at pose jumps.
2. Fuse each chunk into a local cloud (raw poses) with normals.
3. Pose graph: one node per chunk (a rigid correction). Odometry edges between consecutive
   chunks are identity with a moderate weight (odo_info=100, picked by a wall-sharpness sweep:
   1e4 -> 4.13, 1e3 -> 4.26, 1e2 -> 4.85, 1e1 -> 4.75, uncorrected 4.48). Across a pose jump
   the edge is the ICP estimate instead; a jump in the final 2 s drops the tail frames.
4. Loop edges: every non-adjacent chunk pair whose clouds overlap is registered with
   point-to-plane ICP, constrained to yaw + translation (gravity from ARKit is trusted).
   Accepted only when fitness and RMSE pass thresholds; marked uncertain so the optimiser's
   line process can still reject a bad one.
5. Optimise (Open3D global_optimization, LM) and apply each chunk's correction to its frames.
"""
import numpy as np
import open3d as o3d

from . import fuse


def _yaw_only(T):
    """Project a rigid transform to rotation about the world up axis (y) + translation."""
    R = T[:3, :3]
    yaw = np.arctan2(R[0, 2] - R[2, 0], R[0, 0] + R[2, 2])
    c, s = np.cos(yaw), np.sin(yaw)
    out = np.eye(4)
    out[:3, :3] = [[c, 0, s], [0, 1, 0], [-s, 0, c]]
    out[:3, 3] = T[:3, 3]
    return out


def find_jumps(cap, factor=6.0):
    t = cap.T_wc[:, :3, 3]
    sp = np.linalg.norm(np.diff(t, axis=0), axis=1)
    thr = factor * np.percentile(sp, 99)
    return [i + 1 for i in np.where(sp > thr)[0]]


def make_chunks(cap, chunk_s=3.0, fps=60, min_tail_s=2.0):
    """Chunk boundaries. Frames after a pose jump that ends less than `min_tail_s` before the
    end of the capture are dropped: too few frames to register, and ARKit has just relocalised."""
    n = len(cap)
    jumps = find_jumps(cap)
    if jumps and n - jumps[-1] < min_tail_s * fps:
        n = jumps[0] if jumps[-1] - jumps[0] < fps else jumps[-1]
    size = int(chunk_s * fps)
    cuts = set(range(0, n, size)) | {j for j in jumps if j < n} | {n}
    cuts = sorted(cuts)
    chunks = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        if b - a < 10 and chunks:   # tiny remainder: merge into previous
            chunks[-1] = (chunks[-1][0], b)
        else:
            chunks.append((a, b))
    return chunks


def _chunk_cloud(cap, a, b, step=6, voxel=0.04):
    P, _ = fuse.fuse(cap, frame_ids=range(a, b, step), voxel=0.02, min_hits=2)
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(P))
    pc = pc.voxel_down_sample(voxel)
    pc.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=0.12, max_nn=30))
    return pc


def _icp(src, dst, max_dist=0.10):
    res = o3d.pipelines.registration.registration_icp(
        src, dst, max_dist, np.eye(4),
        o3d.pipelines.registration.TransformationEstimationPointToPlane(),
        o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=60))
    T = _yaw_only(res.transformation)
    # re-evaluate with the constrained transform
    ev = o3d.pipelines.registration.evaluate_registration(src, dst, max_dist * 0.5, T)
    info = o3d.pipelines.registration.get_information_matrix_from_point_clouds(src, dst, max_dist * 0.5, T)
    return T, ev.fitness, ev.inlier_rmse, info


def correct(cap, chunk_s=3.0, min_overlap=0.3, min_fitness=0.35, max_rmse=0.03, odo_info=100.0, log=print):
    """Return (corrected poses (N,4,4), valid frame mask (N,), report dict)."""
    chunks = make_chunks(cap, chunk_s)
    jumps = set(find_jumps(cap))
    clouds = [_chunk_cloud(cap, a, b) for a, b in chunks]
    m = len(chunks)
    reg = o3d.pipelines.registration
    graph = reg.PoseGraph()
    for _ in range(m):
        graph.nodes.append(reg.PoseGraphNode(np.eye(4)))

    def overlap(i, j):
        pi, pj = np.asarray(clouds[i].points), np.asarray(clouds[j].points)
        if len(pi) < 200 or len(pj) < 200:
            return 0.0
        tree = o3d.geometry.KDTreeFlann(clouds[j])
        sub = pi[:: max(1, len(pi) // 400)]
        hit = sum(1 for p in sub if tree.search_radius_vector_3d(p, 0.08)[0] > 0)
        return hit / len(sub)

    loops, rejected = [], 0
    for i in range(m - 1):
        a = chunks[i + 1][0]
        if a in jumps:
            T, fit, rmse, info = _icp(clouds[i + 1], clouds[i])
            # chunk i+1 corrected so that it aligns with chunk i: X_i^-1 X_{i+1} = T
            graph.edges.append(reg.PoseGraphEdge(i + 1, i, T, info, uncertain=False))
            log(f"  jump at frame {a}: ICP fit={fit:.2f} rmse={rmse*100:.1f}cm shift={np.linalg.norm(T[:3,3])*100:.1f}cm")
        else:
            graph.edges.append(reg.PoseGraphEdge(i + 1, i, np.eye(4), np.eye(6) * odo_info, uncertain=False))
    for i in range(m):
        for j in range(i + 2, m):
            if overlap(j, i) < min_overlap:
                continue
            T, fit, rmse, info = _icp(clouds[j], clouds[i])
            if fit < min_fitness or rmse > max_rmse:
                rejected += 1
                continue
            graph.edges.append(reg.PoseGraphEdge(j, i, T, info, uncertain=True))
            loops.append((i, j, float(np.linalg.norm(T[:3, 3])), float(fit), float(rmse)))
    log(f"  {m} chunks, {len(loops)} loop closures accepted, {rejected} rejected")
    opt = reg.GlobalOptimizationOption(max_correspondence_distance=0.05, edge_prune_threshold=0.25,
                                       reference_node=0)
    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
    reg.global_optimization(graph, reg.GlobalOptimizationLevenbergMarquardt(),
                            reg.GlobalOptimizationConvergenceCriteria(), opt)
    # loop consistency before (raw odometry) and after optimisation, in cm
    resid_before, resid_after = [], []
    for e in graph.edges:
        if not e.uncertain:
            continue
        Xs, Xt = graph.nodes[e.source_node_id].pose, graph.nodes[e.target_node_id].pose
        resid_before.append(np.linalg.norm(e.transformation[:3, 3]))
        D = np.linalg.inv(Xt) @ Xs @ np.linalg.inv(e.transformation)
        resid_after.append(np.linalg.norm(D[:3, 3]))
    poses = cap.T_wc.copy()
    valid = np.zeros(len(cap), bool)
    corr = []
    for k, (a, b) in enumerate(chunks):
        X = graph.nodes[k].pose
        poses[a:b] = X @ cap.T_wc[a:b]
        valid[a:b] = True
        corr.append(float(np.linalg.norm(X[:3, 3])))
    report = {"loops": loops, "chunks": len(chunks), "loops_accepted": len(loops), "loops_rejected": rejected,
              "pose_jumps": sorted(int(j) for j in jumps),
              "frames_dropped": int((~valid).sum()),
              "loop_misalignment_cm_before": float(np.mean(resid_before) * 100) if resid_before else 0.0,
              "loop_misalignment_cm_after": float(np.mean(resid_after) * 100) if resid_after else 0.0,
              "max_correction_m": max(corr), "mean_correction_m": float(np.mean(corr))}
    return poses, valid, report
