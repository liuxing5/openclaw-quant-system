"""
用户权限系统API
集成到现有Web仪表板的RESTful API
"""

from flask import Blueprint, request, jsonify, g
from flask_cors import cross_origin
from datetime import datetime, timedelta
from typing import Dict, Any, List
import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base, User, Role, Permission, Team, APIToken, AuditLog
from .auth import AuthService, RBACManager, AuthConfig

# 创建数据库连接
DATABASE_URL = "sqlite:///./quant_system/auth/auth.db"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建数据库表
Base.metadata.create_all(bind=engine)

# 创建蓝图
auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')

# 全局数据库会话
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_db():
    """获取当前请求的数据库会话"""
    if 'db' not in g:
        g.db = SessionLocal()
    return g.db


def close_db(e=None):
    """关闭数据库连接"""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def audit_log(action: str, resource_type: str = None, resource_id: str = None, 
              details: str = None, user_id: int = None, username: str = None):
    """记录审计日志"""
    db = get_current_db()
    
    log = AuditLog(
        user_id=user_id,
        username=username,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string if request.user_agent else None,
        timestamp=datetime.utcnow()
    )
    
    db.add(log)
    db.commit()


def require_auth():
    """认证装饰器"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            db = get_current_db()
            auth_service = AuthService(db)
            
            # 从请求头获取令牌
            auth_header = request.headers.get('Authorization')
            if not auth_header:
                return jsonify({'error': '未提供认证令牌'}), 401
            
            # 支持Bearer令牌和API令牌
            if auth_header.startswith('Bearer '):
                token = auth_header[7:]
                user = auth_service.get_current_user(token)
            else:
                # 直接使用API令牌
                user = auth_service.verify_api_token(auth_header)
            
            if not user:
                return jsonify({'error': '无效或过期的令牌'}), 401
            
            # 将当前用户存储在g对象中
            g.current_user = user
            g.db = db
            
            return func(*args, **kwargs)
        wrapper.__name__ = func.__name__
        return wrapper
    return decorator


def require_permission(permission_name: str):
    """权限检查装饰器"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            if not hasattr(g, 'current_user'):
                return jsonify({'error': '用户未认证'}), 401
            
            db = get_current_db()
            auth_service = AuthService(db)
            
            if not auth_service.check_permission(g.current_user, permission_name):
                audit_log(
                    action='permission_denied',
                    resource_type='api',
                    resource_id=permission_name,
                    details=f'用户 {g.current_user.username} 尝试访问需要权限 {permission_name} 的API',
                    user_id=g.current_user.id,
                    username=g.current_user.username
                )
                return jsonify({'error': '权限不足'}), 403
            
            return func(*args, **kwargs)
        wrapper.__name__ = func.__name__
        return wrapper
    return decorator


@auth_bp.route('/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'service': 'auth-api'
    })


@auth_bp.route('/initialize', methods=['POST'])
def initialize_system():
    """初始化系统（创建默认角色和权限）"""
    db = get_current_db()
    rbac_manager = RBACManager(db)
    
    try:
        rbac_manager.initialize_defaults()
        audit_log('system_initialized', 'system', 'all', '系统权限初始化')
        
        return jsonify({
            'success': True,
            'message': '系统初始化成功',
            'default_admin': {
                'username': 'admin',
                'password': 'admin123',
                'note': '请立即修改默认密码'
            }
        })
    except Exception as e:
        return jsonify({'error': f'初始化失败: {str(e)}'}), 500


@auth_bp.route('/register', methods=['POST'])
def register_user():
    """用户注册"""
    db = get_current_db()
    data = request.get_json()
    
    if not data:
        return jsonify({'error': '无效的请求数据'}), 400
    
    required_fields = ['username', 'email', 'password', 'full_name']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'缺少必要字段: {field}'}), 400
    
    # 检查用户名和邮箱是否已存在
    existing_user = db.query(User).filter(
        (User.username == data['username']) | (User.email == data['email'])
    ).first()
    
    if existing_user:
        return jsonify({'error': '用户名或邮箱已存在'}), 409
    
    # 创建新用户
    user = User(
        username=data['username'],
        email=data['email'],
        full_name=data['full_name']
    )
    user.set_password(data['password'])
    
    # 分配默认角色（观察员）
    default_role = db.query(Role).filter(Role.name == 'observer').first()
    if default_role:
        user.roles.append(default_role)
    
    db.add(user)
    db.commit()
    
    audit_log('user_registered', 'user', str(user.id), 
              f'新用户注册: {user.username}', user.id, user.username)
    
    return jsonify({
        'success': True,
        'message': '用户注册成功',
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'full_name': user.full_name
        }
    }), 201


@auth_bp.route('/login', methods=['POST'])
def login():
    """用户登录"""
    db = get_current_db()
    data = request.get_json()
    
    if not data:
        return jsonify({'error': '无效的请求数据'}), 400
    
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400
    
    auth_service = AuthService(db)
    user = auth_service.authenticate_user(username, password)
    
    if not user:
        audit_log('login_failed', 'auth', 'login', 
                  f'登录失败: {username}')
        return jsonify({'error': '用户名或密码错误'}), 401
    
    # 创建访问令牌
    access_token = auth_service.create_access_token(user)
    
    audit_log('login_success', 'auth', 'login', 
              f'用户登录成功', user.id, user.username)
    
    return jsonify({
        'access_token': access_token,
        'token_type': 'bearer',
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'full_name': user.full_name,
            'is_superuser': user.is_superuser,
            'roles': [role.name for role in user.roles]
        }
    })


@auth_bp.route('/me', methods=['GET'])
@require_auth()
def get_current_user_info():
    """获取当前用户信息"""
    user = g.current_user
    
    return jsonify({
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'full_name': user.full_name,
            'is_active': user.is_active,
            'is_superuser': user.is_superuser,
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'last_login': user.last_login.isoformat() if user.last_login else None,
            'roles': [{
                'id': role.id,
                'name': role.name,
                'description': role.description
            } for role in user.roles],
            'teams': [{
                'id': team.id,
                'name': team.name,
                'description': team.description
            } for team in user.teams]
        }
    })


@auth_bp.route('/users', methods=['GET'])
@require_auth()
@require_permission('user.read')
def list_users():
    """列出所有用户（需要权限）"""
    db = get_current_db()
    users = db.query(User).all()
    
    audit_log('users_listed', 'user', 'all', 
              f'用户列表查询', g.current_user.id, g.current_user.username)
    
    return jsonify({
        'users': [{
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'full_name': user.full_name,
            'is_active': user.is_active,
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'roles': [role.name for role in user.roles]
        } for user in users]
    })


@auth_bp.route('/users/<int:user_id>', methods=['GET'])
@require_auth()
@require_permission('user.read')
def get_user(user_id: int):
    """获取特定用户信息"""
    db = get_current_db()
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    
    audit_log('user_viewed', 'user', str(user_id), 
              f'查看用户信息: {user.username}', g.current_user.id, g.current_user.username)
    
    return jsonify({
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'full_name': user.full_name,
            'is_active': user.is_active,
            'is_superuser': user.is_superuser,
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'last_login': user.last_login.isoformat() if user.last_login else None,
            'roles': [{
                'id': role.id,
                'name': role.name,
                'description': role.description
            } for role in user.roles]
        }
    })


@auth_bp.route('/roles', methods=['GET'])
@require_auth()
@require_permission('user.read')
def list_roles():
    """列出所有角色"""
    db = get_current_db()
    roles = db.query(Role).all()
    
    return jsonify({
        'roles': [{
            'id': role.id,
            'name': role.name,
            'description': role.description,
            'is_default': role.is_default,
            'permissions': [{
                'id': perm.id,
                'name': perm.name,
                'description': perm.description
            } for perm in role.permissions]
        } for role in roles]
    })


@auth_bp.route('/permissions', methods=['GET'])
@require_auth()
@require_permission('user.read')
def list_permissions():
    """列出所有权限"""
    db = get_current_db()
    permissions = db.query(Permission).all()
    
    return jsonify({
        'permissions': [{
            'id': perm.id,
            'name': perm.name,
            'description': perm.description,
            'resource_type': perm.resource_type,
            'action': perm.action
        } for perm in permissions]
    })


@auth_bp.route('/teams', methods=['GET'])
@require_auth()
@require_permission('team.read')
def list_teams():
    """列出所有团队"""
    db = get_current_db()
    teams = db.query(Team).all()
    
    audit_log('teams_listed', 'team', 'all', 
              f'团队列表查询', g.current_user.id, g.current_user.username)
    
    return jsonify({
        'teams': [{
            'id': team.id,
            'name': team.name,
            'description': team.description,
            'is_active': team.is_active,
            'created_at': team.created_at.isoformat() if team.created_at else None,
            'member_count': len(team.members),
            'members': [{
                'id': member.id,
                'username': member.username,
                'email': member.email
            } for member in team.members]
        } for team in teams]
    })


@auth_bp.route('/teams', methods=['POST'])
@require_auth()
@require_permission('team.create')
def create_team():
    """创建团队"""
    db = get_current_db()
    data = request.get_json()
    
    if not data or 'name' not in data:
        return jsonify({'error': '团队名称不能为空'}), 400
    
    # 检查团队名称是否已存在
    existing_team = db.query(Team).filter(Team.name == data['name']).first()
    if existing_team:
        return jsonify({'error': '团队名称已存在'}), 409
    
    # 创建团队
    team = Team(
        name=data['name'],
        description=data.get('description', ''),
        is_active=data.get('is_active', True)
    )
    
    # 添加创建者为团队成员
    team.members.append(g.current_user)
    
    db.add(team)
    db.commit()
    
    audit_log('team_created', 'team', str(team.id), 
              f'创建团队: {team.name}', g.current_user.id, g.current_user.username)
    
    return jsonify({
        'success': True,
        'message': '团队创建成功',
        'team': {
            'id': team.id,
            'name': team.name,
            'description': team.description,
            'is_active': team.is_active
        }
    }), 201


@auth_bp.route('/api-tokens', methods=['GET'])
@require_auth()
def list_api_tokens():
    """列出用户的API令牌"""
    db = get_current_db()
    tokens = db.query(APIToken).filter(
        APIToken.user_id == g.current_user.id
    ).all()
    
    return jsonify({
        'tokens': [{
            'id': token.id,
            'name': token.name,
            'token': token.token[:8] + '...' if len(token.token) > 8 else token.token,
            'is_active': token.is_active,
            'last_used': token.last_used.isoformat() if token.last_used else None,
            'expires_at': token.expires_at.isoformat() if token.expires_at else None,
            'created_at': token.created_at.isoformat() if token.created_at else None
        } for token in tokens]
    })


@auth_bp.route('/api-tokens', methods=['POST'])
@require_auth()
def create_api_token():
    """创建API令牌"""
    db = get_current_db()
    data = request.get_json()
    
    if not data or 'name' not in data:
        return jsonify({'error': '令牌名称不能为空'}), 400
    
    auth_service = AuthService(db)
    
    # 获取权限列表
    permissions = data.get('permissions', [])
    
    # 创建API令牌
    api_token = auth_service.create_api_token(
        user=g.current_user,
        name=data['name'],
        permissions=permissions,
        expires_days=data.get('expires_days', 365)
    )
    
    audit_log('api_token_created', 'api_token', str(api_token.id), 
              f'创建API令牌: {api_token.name}', g.current_user.id, g.current_user.username)
    
    return jsonify({
        'success': True,
        'message': 'API令牌创建成功',
        'token': {
            'id': api_token.id,
            'name': api_token.name,
            'token': api_token.token,  # 注意：只在创建时返回完整令牌
            'expires_at': api_token.expires_at.isoformat() if api_token.expires_at else None,
            'permissions': api_token.permissions
        }
    }), 201


@auth_bp.route('/audit-logs', methods=['GET'])
@require_auth()
@require_permission('system.admin')
def get_audit_logs():
    """获取审计日志（仅管理员）"""
    db = get_current_db()
    
    # 查询参数
    page = int(request.args.get('page', 1))
    per_page = min(int(request.args.get('per_page', 50)), 100)
    user_id = request.args.get('user_id')
    action = request.args.get('action')
    
    query = db.query(AuditLog).order_by(AuditLog.timestamp.desc())
    
    if user_id:
        query = query.filter(AuditLog.user_id == user_id)
    if action:
        query = query.filter(AuditLog.action == action)
    
    # 分页
    total = query.count()
    logs = query.offset((page - 1) * per_page).limit(per_page).all()
    
    return jsonify({
        'page': page,
        'per_page': per_page,
        'total': total,
        'logs': [{
            'id': log.id,
            'user_id': log.user_id,
            'username': log.username,
            'action': log.action,
            'resource_type': log.resource_type,
            'resource_id': log.resource_id,
            'details': log.details,
            'ip_address': log.ip_address,
            'timestamp': log.timestamp.isoformat() if log.timestamp else None
        } for log in logs]
    })


@auth_bp.route('/check-permission/<permission_name>', methods=['GET'])
@require_auth()
def check_permission(permission_name: str):
    """检查当前用户是否有特定权限"""
    db = get_current_db()
    auth_service = AuthService(db)
    
    has_perm = auth_service.check_permission(g.current_user, permission_name)
    
    return jsonify({
        'has_permission': has_perm,
        'permission': permission_name,
        'user': {
            'id': g.current_user.id,
            'username': g.current_user.username,
            'is_superuser': g.current_user.is_superuser
        }
    })


@auth_bp.route('/my-permissions', methods=['GET'])
@require_auth()
def get_my_permissions():
    """获取当前用户的所有权限"""
    db = get_current_db()
    auth_service = AuthService(db)
    
    permissions = auth_service.get_user_permissions(g.current_user)
    
    return jsonify({
        'permissions': permissions,
        'user': {
            'id': g.current_user.id,
            'username': g.current_user.username,
            'is_superuser': g.current_user.is_superuser
        }
    })


# 集成到现有Flask应用的帮助函数
def init_auth_system(app):
    """初始化认证系统"""
    # 注册蓝图
    app.register_blueprint(auth_bp)
    
    # 添加数据库清理中间件
    app.teardown_appcontext(close_db)
    
    # 初始化默认数据（如果数据库为空）
    with app.app_context():
        db = SessionLocal()
        try:
            # 检查是否需要初始化
            user_count = db.query(User).count()
            if user_count == 0:
                print("初始化认证系统默认数据...")
                rbac_manager = RBACManager(db)
                rbac_manager.initialize_defaults()
                print("认证系统初始化完成")
        finally:
            db.close()
    
    return app